# Autofill Cashew

Unsurprisingly, Autofill Cashew automates filling in purchase entries in 
[Cashew](https://github.com/jameskokoska/Cashew) using 
[app links](https://github.com/jameskokoska/Cashew?tab=readme-ov-file#app-links).

Usually, banks allow the option to turn on account alerts to send emails for
transactions that occur on credit cards. Autofill Cashew reads these emails and
constructs an app link which once tapped on the phone will fill the entries in Cashew.
Purchases are assigned to categories using ChatGPT.

### Prerequisites 
1. The script expects you to be using gmail for your bank services. 
2. The user needs to run the script on a Mac, and must have an iPhone.
3. Currently, only Scotiabank is supported.

### Running autofill-cashew.py
Download all dependencies and activate the conda env:
```
conda create --name autofill-cashew 
conda activate autofill-cashew
conda install toml pathlib bs4 openai python-dateutil

python -m pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

A `config.toml` is necessary for the script. The following example lists all available fields:
```
PHONE = "<your phone number>"
OPENAI_API_KEY = ""
CACHE_PATH = "cache.json"

CATEGORY_LIST = [
    "Dining",
    "Groceries",
]
```
The `OPENAI_API_KEY` is not currently used in `config.toml`, but will be in the future.

Follow the "Set up your environment" section in the 
[Gmail API quickstart guide](https://developers.google.com/gmail/api/quickstart/python). 
Note that the token will eventually timeout, and you will have to log in periodically
when running the script.

Create a cache file ("cache.json"): 
```
{"last_seen_id":"", "category_cache":{}}
```

Export your environment variables `CONFIG_PATH` to the path to the config file, and 
and `OPENAI_API_KEY` to your api key.

Now you are ready to run the script:
```
python3 autofill-cashew.py
```
The script should prompt you to log into your gmail account, and afterwards send you a 
message with an app link. Tap on the app link on your phone, and Cashew should open up
with tranactions populated. 


