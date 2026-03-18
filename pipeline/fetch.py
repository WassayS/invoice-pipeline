import os
import time
import random
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv("QB_CLIENT_ID")
CLIENT_SECRET = os.getenv("QB_CLIENT_SECRET")
REALM_ID      = os.getenv("QB_REALM_ID")
REFRESH_TOKEN = os.getenv("QB_REFRESH_TOKEN")

# Errors worth retrying — server-side or rate limit
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Errors that will never succeed on retry — fail immediately
NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404}


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
    response.raise_for_status()
    tokens = response.json()
    print("Token refreshed successfully")
    return tokens["access_token"]


def fetch_with_retry(url, headers, params, max_retries=4):
    """
    Production-grade HTTP fetch with:
    - Exponential backoff: wait = base * (2^attempt)
    - Full jitter: randomised within [0, wait] to prevent thundering herd
    - Retry-After header respected on 429
    - Non-retryable errors fail immediately — no wasted retries
    - Detailed logging on every attempt
    """
    base_wait = 1.0
    max_wait  = 30.0

    for attempt in range(max_retries):
        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 200:
            return response

        # Respect Retry-After header if present (QB sends this on 429)
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 10))
            print(f"Rate limited — QB says wait {retry_after}s")
            time.sleep(retry_after)
            continue

        # Non-retryable — fail immediately, no point retrying
        if response.status_code in NON_RETRYABLE_STATUS_CODES:
            raise Exception(
                f"Non-retryable error {response.status_code} — "
                f"will not retry: {response.text}"
            )

        # Retryable server error — exponential backoff with full jitter
        if response.status_code in RETRYABLE_STATUS_CODES:
            if attempt == max_retries - 1:
                raise Exception(
                    f"API failed after {max_retries} attempts. "
                    f"Last status: {response.status_code}"
                )
            exponential_wait = min(base_wait * (2 ** attempt), max_wait)
            jittered_wait    = random.uniform(0, exponential_wait)
            print(
                f"Attempt {attempt + 1} failed ({response.status_code}) — "
                f"retrying in {jittered_wait:.1f}s "
                f"(exponential={exponential_wait:.1f}s, jitter applied)"
            )
            time.sleep(jittered_wait)
            continue

        # Unknown status — fail immediately
        raise Exception(
            f"Unexpected status {response.status_code}: {response.text}"
        )

    raise Exception(f"API failed after {max_retries} attempts")


def fetch_invoices(access_token, since=None):
    url = (
        f"https://sandbox-quickbooks.api.intuit.com"
        f"/v3/company/{REALM_ID}/query"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    all_invoices   = []
    page_size      = 100
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
                f"STARTPOSITION {start_position} "
                f"MAXRESULTS {page_size}"
            )

        response = fetch_with_retry(url, headers, params={"query": query})
        data     = response.json()
        invoices = data.get("QueryResponse", {}).get("Invoice", [])
        all_invoices.extend(invoices)

        print(
            f"Fetched page at position {start_position} "
            f"— {len(invoices)} records"
        )

        if len(invoices) < page_size:
            break

        start_position += page_size

    print(f"Total fetched: {len(all_invoices)} invoices")
    return all_invoices