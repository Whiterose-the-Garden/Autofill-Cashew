# https://github.com/googleworkspace/python-samples/tree/master/gmail/quickstart

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
from datetime import datetime

# https://medium.com/@jameskabbes/sending-imessages-with-python-on-a-mac-b77b7dd6e371
# os.system('osascript <ScriptPath> <Argument1> ... <ArgumentN>')

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

CONFIG_PATH = "config.toml"
CATEGORY_LIST = None
CACHE_PATH = None
ACCOUNT_NUMBER_TO_CASHEW_ACCOUNT = {}

CASHEW_ROUTE = "https://cashewapp.web.app"

# For google authentication
TOKEN_PATH="token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

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
# TODO: This should really use the timestamp of either whatever
# was reported by email, or the date/time the email was sent.
def get_date(date_str):
    dt = datetime.now()
    if date_str[-2:] in ("am","pm"):
        t = datetime.strptime(date_str.upper(), "%I:%M %p")
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
     
def parse_scotia_statement(soup, oai_client, category_cache):
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

    date_str = match.group(4)
    # Scotiabank can have e.g. `0:53 am`...
    if date_str[0] == "0":
        date_str = "12" + date_str[1:]
    print(date_str)

    return {
        # Remove `$` at the front.
        "amount": "-" + match.group(1)[1:],
        "title": match.group(2),
        "date": get_date(date_str),
        "category": get_category(match.group(2), oai_client, category_cache),
        "account": get_account(match.group(3)),
    }


def parse_cibc_statement(str):
    pass

def parse_amex_statement(str):
    pass

def parse_rbc_statement(str):
    pass

# Read https://stackoverflow.com/questions/24745006/gmail-api-parse-message-content-base64-decoding-with-javascript to see why we need to do these replacements.

# TODO: Log certain strings in a file for future debugging
def warn(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


# From: https://medium.com/@jameskabbes/sending-imessages-with-python-on-a-mac-b77b7dd6e371
def send_populating_message(processed_transactions):
    SCRIPT_PATH="send_imessage.applescript"
    phone = os.environ.get("PHONE")
    if not phone:
        warn("Phone number has not been set!")
        raise SystemExit

    json_format = {
        "transactions": processed_transactions
    }
    json_str = json.dumps(json_format)
    print(json.dumps(json_format, indent=4))
    url = f"{CASHEW_ROUTE}/addTransaction?JSON={quote(json_str)}"
    print(url)
    os.system(f"osascript {SCRIPT_PATH} {os.environ['PHONE']} {url}")
    

def get_parser(msg_str):

    for header in msg_str["header"]:

        sender = header.get("name")
        if sender != "From":
            continue

        if sender not in EMAIL_TO_PARSER:
            warn(f"Cannot find a parser for email `{sender}`.")
            continue

        return EMAIL_TO_PARSER[sender]

    warn(f"DID NOT FIND ANY PARSER FOR SENDER!")



# for p in soup.find_all('p'):
    # if p.text[:len(SB_MATCH)] != SB_MATCH:
        # $43.62 at MANGO on account at 2:51 pm ET 

# Parse a gmail message "body" to dictionary form as required for Cashew
def body_to_cashew_dict(body, bank, oai_client, category_cache):
    email_body = b64decode(body["data"].replace('-', '+').replace('_','/'))
    soup = BeautifulSoup(email_body, "html.parser")
    match bank:
        case Bank.SB:
            return parse_scotia_statement(soup, oai_client, category_cache)
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
            print(config)
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


# TODO: Config file, read and populate instead as well as default values
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
                    .list(userId="me", q=query, maxResults=10)
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
            
            # Gmail rate limits messages.get() to 50 queries per second (See
            # https://developers.google.com/gmail/api/reference/quota). Sleep
            # here so that you don't reach that limit. We might be spending
            # enough time doing actual work in each iteration that this sleep
            # unnecessary, and inaccuracy of sleep doesn't make us hit the 
            # rate limit.
            sleep(0.02)
            cashew_dict = body_to_cashew_dict(msg["body"], bank, oai_client,
                                              category_cache)
            processed_transactions.append(cashew_dict)

        if processed_transactions:
            send_populating_message(processed_transactions)
            # The last message in the list is the newest message.
            last_seen_id = last_seen_id if not message_ids else message_ids[-1]
            save_cache(last_seen_id, category_cache)

    except HttpError as error:
        print(f"An error occured: {error}")

if __name__ == "__main__":
    main()
