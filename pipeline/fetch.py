import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("QB_CLIENT_ID")
CLIENT_SECRET = os.getenv("QB_CLIENT_SECRET")
REALM_ID = os.getenv("QB_REALM_ID")
ACCESS_TOKEN = os.getenv("QB_ACCESS_TOKEN")
REFRESH_TOKEN = os.getenv("QB_REFRESH_TOKEN")


def refresh_access_token():
    url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    response = requests.post(
        url,
        auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET),
        headers={"Accept": "application/json"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN
        }
    )
    tokens = response.json()
    print("Token refreshed successfully")
    return tokens["access_token"]


def fetch_invoices(access_token, since=None):
    url = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{REALM_ID}/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    all_invoices = []
    page_size = 100
    start_position = 1

    while True:
        if since:
            query = (
                f"SELECT * FROM Invoice "
                f"WHERE MetaData.LastUpdatedTime > '{since}' "
                f"STARTPOSITION {start_position} MAXRESULTS {page_size}"
            )
        else:
            query = (
                f"SELECT * FROM Invoice "
                f"STARTPOSITION {start_position} MAXRESULTS {page_size}"
            )

        response = requests.get(url, headers=headers, params={"query": query})
        data = response.json()
        invoices = data.get("QueryResponse", {}).get("Invoice", [])
        all_invoices.extend(invoices)

        print(f"Fetched page starting at {start_position} — got {len(invoices)} records")

        if len(invoices) < page_size:
            break

        start_position += page_size

    print(f"Total fetched: {len(all_invoices)} invoices")
    return all_invoices

if __name__ == "__main__":
    token = refresh_access_token()
    invoices = fetch_invoices(token)
    for inv in invoices:
        print(inv)