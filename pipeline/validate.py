import os
import requests
from requests.auth import HTTPBasicAuth
from supabase import create_client
from dotenv import load_dotenv
from fetch import refresh_access_token

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
REALM_ID = os.getenv("QB_REALM_ID")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_qb_invoice_count(access_token):
    url = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{REALM_ID}/query"
    query = "SELECT COUNT(*) FROM Invoice"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    response = requests.get(url, headers=headers, params={"query": query})
    data = response.json()
    count = data.get("QueryResponse", {}).get("totalCount", 0)
    return count


def get_supabase_invoice_count():
    result = supabase.table("invoices")\
        .select("id", count="exact")\
        .is_("deleted_at", "null")\
        .execute()
    return result.count


def validate():
    print("Running validation...")
    token = refresh_access_token()

    qb_count = get_qb_invoice_count(token)
    sb_count = get_supabase_invoice_count()

    print(f"QuickBooks count : {qb_count}")
    print(f"Supabase count   : {sb_count}")

    if qb_count == sb_count:
        print("Validation PASSED — counts match")
        return True
    else:
        diff = abs(qb_count - sb_count)
        print(f"Validation FAILED — mismatch of {diff} records")
        return False


if __name__ == "__main__":
    validate()