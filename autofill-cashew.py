import os
import toml
from datetime import datetime
from time import sleep
from enum import Enum
from base64 import b64decode
import sys
import re
import json
from urllib.parse import quote
from pathlib import Path
from itertools import chain
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openai import OpenAI
from dateutil import parser

def error(*args, **kwargs):
    warn(*args, **kwargs)
    sys.exit(1)

class Bank(Enum):
    SB = 1
    AMEX = 2
    CIBC = 3
    RBC = 4

BANK_TO_EMAIL = {
    Bank.SB: "infoalerts@scotiabank.com",
    Bank.AMEX: "",
    Bank.CIBC: "",
    Bank.RBC: "",
}

CONFIG_PATH = os.environ.get("CONFIG_PATH")
CATEGORY_LIST = None
CACHE_PATH = None
ACCOUNT_NUMBER_TO_CASHEW_ACCOUNT = {}
PHONE = None

CASHEW_ROUTE = "https://cashewapp.web.app"

# For google authentication
TOKEN_PATH="token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# https://github.com/googleworkspace/python-samples/tree/master/gmail/quickstart
def authenticate():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())
    return creds

def get_account(acc):
    return ACCOUNT_NUMBER_TO_CASHEW_ACCOUNT[acc]

# Format the date and time to "MM/dd/yyyy HH:mm:ss"
def get_datetime(time_str, dt):
    if time_str[-2:] in ("am","pm"):
        t = datetime.strptime(time_str.upper(), "%I:%M %p")
        dt = dt.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    return dt.strftime("%m/%d/%Y %H:%M:%S")

def get_category(name, oai_client, category_cache):

    if name not in category_cache:
        query = (
            f"{name} purchase belong to: {', '.join(CATEGORY_LIST)}? "
            "Respond only by category."
        )
        completion = oai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                { "role": "user", 
                  "content": query,
                },
            ]
        )
        temp = completion.choices[0].message.content
        if len(temp) > 0 and not temp[-1].isalnum():
            temp = temp[:len(temp) - 1]

        category_cache[name] = temp

    return category_cache[name]
     
def parse_scotia_statement(soup, oai_client, category_cache, date):
    # For an example, see "Downloads/Authorization on your credit card outside of Canada.eml CBRE Limited Transaction Approved.eml" 
    SB_MATCH = "There was an authorization"
    pattern = r"(\$\d+\.\d{2}) at (.+) on account (\d{4}\*+\d{3}\*+) at (\d{1,2}:\d{2} [ap]m)"
    transaction_text = ""
    for p in soup.find_all('p'):
        if p.text[:len(SB_MATCH)] == SB_MATCH:
            transaction_text = p.text
            break
    
    if not transaction_text:
        warn(f"Could not find the transaction text in:\n {soup.prettify()}")
        return
    # IF the hour position is single digit (e.g. 6:50 pm), then the text 
    # doesn't strink by 1 space, but is replaced by an additional space
    # (e.g. ` 6:50 pm`).
    transaction_text = re.sub(r'\s+', ' ', transaction_text)

    match = re.search(pattern, transaction_text)

    time_str = match.group(4)
    # Scotiabank can have e.g. `0:53 am`...
    if time_str[0] == "0":
        time_str = "12" + time_str[1:]

    # There seems to be a bug with iMessage such that (some) periods make the
    # deep link not be interpreted as a link, and thus cannot be conveniently
    # tapped on. Convert all periods to its url encoding "%2E".
    # TODO: Note that urllib.parse.quote will never replace `.`; is there a
    # url encoding function that will?
    # The [1:] removes `$` at the front.
    amount = "-" + "%2E".join(match.group(1)[1:].split("."))
    return {
        "date": get_datetime(time_str, date),
        "amount": amount,
        "title": match.group(2),
        "category": get_category(match.group(2), oai_client, category_cache),
    }


def parse_cibc_statement(str):
    pass

def parse_amex_statement(str):
    pass

def parse_rbc_statement(str):
    pass

def warn(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def send_populating_message(processed_transactions):
    SCRIPT_PATH="send_imessage.applescript"
    MAX_MESSAGES = 3
    json_format = {
        "transactions": processed_transactions
    }
    json_str = json.dumps(json_format)
    url = f"{CASHEW_ROUTE}/addTransaction?JSON={quote(json_str)}"
    os.system(f"osascript {SCRIPT_PATH} {PHONE} {url}")

# Return datetime object of the time the email was sent.
def get_date(headers):
    for h in headers:

        name = h.get("name")
        if name != "Date":
            continue

        return parser.parse(h.get("value"))

    return datetime.now()

# Parse a gmail message "body" to dictionary form as required for Cashew
def body_to_cashew_dict(body, bank, oai_client, category_cache, date):
    email_body = b64decode(body["data"].replace('-', '+').replace('_','/'))
    soup = BeautifulSoup(email_body, "html.parser")
    match bank:
        case Bank.SB:
            return parse_scotia_statement(soup, oai_client, category_cache, date)
        case _:
            warn(f"Given Bank is unsupported: {bank} (should be one of {BANK_TO_EMAIL.keys()})")
    

# JSON format of cache:
# {
#   "last_seen_id": <id>
#   "category_cache": [
#       <title>: <category>
#   ]
# }
def load_cache():
    cache = {}
    if Path(CACHE_PATH).is_file() and os.stat(CACHE_PATH).st_size != 0:
        with open(CACHE_PATH, 'r') as f:
            cache = json.load(f)
    else:
        warn(f"{CACHE_PATH} does not exist, or is an empty file.")

    if "last_seen_id" not in cache:
        cache["last_seen_id"] = ""
    if "category_map" not in cache:
        cache["category_map"] = {}

    return cache["last_seen_id"], cache["category_cache"]

def save_cache(last_seen_id, category_cache):
    cache = {
        "last_seen_id": last_seen_id,
        "category_cache": category_cache
    }
    with open(CACHE_PATH, 'w') as f:
        json_str = json.dump(cache, f)
    
def load_config():
    global PHONE, OPENAI_API_KEY, CACHE_PATH, CATEGORY_LIST
    global ACCOUNT_NUMBER_TO_CASHEW_ACCOUNT

    # Default values
    CACHE_PATH = "cache.json"
    # TODO: Should be synced with the cashew app.
    CATEGORY_LIST = [
        "Dining",
        "Groceries",
        "Transit",
        "Entertainment",
        "Bills & Fees",
        "Beauty",
        "Travel",
        "Tech",
        "Health",
        "Shopping",
        "Utility",
    ]
    ACCOUNT_NUMBER_TO_CASHEW_ACCOUNT = {}

    config = {}
    if Path(CONFIG_PATH).is_file() and os.stat(CONFIG_PATH).st_size != 0:
        with open(CONFIG_PATH, "r") as f:
            config = toml.load(f)
        PHONE = config.get("PHONE") or PHONE
        OPENAI_API_KEY = config.get("OPENAI_API_KEY") or OPENAI_API_KEY
        CACHE_PATH = config.get("CACHE_PATH") or CACHE_PATH
        CATEGORY_LIST = config.get("CATEGORY_LIST") or CATEGORY_LIST
        ACCOUNT_NUMBER_TO_CASHEW_ACCOUNT = (
            config.get("ACCOUNT_NUMBER_TO_CASHEW_ACCOUNT") or 
            ACCOUNT_NUMBER_TO_CASHEW_ACCOUNT
        )
    else:
        warn(f"{CONFIG_PATH} does not exist, or is an empty file.")

    if not PHONE:
        error("Missing PHONE number!")
    if not OPENAI_API_KEY:
        error("Missing OpenAI API key!")

# Given a header list, figure out which bank it came from
def get_bank(headers):
    pair = []
    for h in headers:
        if "name" not in h or "value" not in h:
            continue

        if h["name"] != "From":
            continue

        pair = list(filter(lambda x: x[1] in h["value"], list(BANK_TO_EMAIL.items())))

        if pair:
            break

    if not pair:
        warn(f"Could not determine the bank from header! Given headers are {headers}")

    return pair[0][0]


def main():

    creds = authenticate()
    load_config()
    last_seen_id, category_cache = load_cache()
    oai_client = OpenAI()
    try:
        processed_transactions = []
        service = build("gmail", "v1", credentials=creds)
        non_empty_emails = filter(lambda x: len(x) > 0, BANK_TO_EMAIL.values())
        query = " OR ".join(
            map(lambda x: f"from:{x}", non_empty_emails)
        )
        results = (
            service.users().messages()
                    .list(userId="me", q=query, maxResults=50)
                    .execute()
                    .get("messages", [])
        )
        message_ids = list(
            chain(map(lambda x: x["id"], results), [last_seen_id]))
        message_ids = message_ids[:message_ids.index(last_seen_id)]
        # Reverse the list so that the oldest unseen message is first in the list.
        message_ids.reverse()

        for id in message_ids:
            msg = (
                service.users().messages().get(userId="me", id=id)
                                                    .execute()["payload"]
            )
            bank = get_bank(msg["headers"])
            date = get_date(msg["headers"])
            
            # Gmail rate limits messages.get() to 50 queries per second (See
            # https://developers.google.com/gmail/api/reference/quota). Sleep
            # here so that you don't reach that limit. We might be spending
            # enough time doing actual work in each iteration that this sleep
            # is unnecessary, or inaccuracy of sleep doesn't make us hit the
            # rate limit.
            sleep(0.01)
            cashew_dict = body_to_cashew_dict(msg["body"], bank, oai_client,
                                              category_cache, date)

            # The email may not contain transaction information.
            if cashew_dict:
                processed_transactions.append(cashew_dict)

        if processed_transactions:
            send_populating_message(processed_transactions)
            # The last message in the list is the newest message.
            last_seen_id = last_seen_id if not message_ids else message_ids[-1]
            save_cache(last_seen_id, category_cache)

    except HttpError as error:
        error(f"An error occured: {error}")

if __name__ == "__main__":
    main()
