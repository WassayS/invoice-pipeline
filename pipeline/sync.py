import os
import time
from datetime import timezone, datetime
from supabase import create_client
from dotenv import load_dotenv
from pipeline.fetch import refresh_access_token, fetch_invoices
from validate import validate

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_watermark():
    result = supabase.table("sync_watermarks")\
        .select("last_synced_at")\
        .eq("key", "invoices")\
        .single()\
        .execute()
    return result.data["last_synced_at"]


def update_watermark(timestamp):
    supabase.table("sync_watermarks")\
        .update({"last_synced_at": timestamp})\
        .eq("key", "invoices")\
        .execute()
    print(f"Watermark advanced to {timestamp}")


def determine_status(balance, amount):
    if balance == 0:
        return "Paid"
    elif balance < amount:
        return "Partial"
    else:
        return "Unpaid"


def transform_invoice(inv):
    amount = float(inv.get("TotalAmt", 0))
    balance = float(inv.get("Balance", 0))
    return {
        "id": str(inv["Id"]),
        "doc_number": inv.get("DocNumber"),
        "customer_id": inv.get("CustomerRef", {}).get("value"),
        "customer_name": inv.get("CustomerRef", {}).get("name", "Unknown"),
        "amount": amount,
        "balance": balance,
        "currency": inv.get("CurrencyRef", {}).get("value", "USD"),
        "status": determine_status(balance, amount),
        "payment_terms": inv.get("SalesTermRef", {}).get("name"),
        "email": inv.get("BillEmail", {}).get("Address"),
        "sync_token": inv.get("SyncToken"),
        "issue_date": inv.get("TxnDate"),
        "due_date": inv.get("DueDate"),
        "last_updated_qb": inv.get("MetaData", {}).get("LastUpdatedTime"),
    }


def transform_line_items(inv):
    items = []
    for line in inv.get("Line", []):
        if line.get("DetailType") == "SubTotalLineDetail":
            continue
        detail = line.get("SalesItemLineDetail", {})
        items.append({
            "invoice_id": str(inv["Id"]),
            "line_number": int(line.get("LineNum", 1)),
            "detail_type": line.get("DetailType"),
            "description": line.get("Description"),
            "item_id": detail.get("ItemRef", {}).get("value"),
            "item_name": detail.get("ItemRef", {}).get("name"),
            "quantity": float(detail["Qty"]) if detail.get("Qty") else None,
            "unit_price": float(detail["UnitPrice"]) if detail.get("UnitPrice") else None,
            "amount": float(line.get("Amount", 0)),
            "tax_code": detail.get("TaxCodeRef", {}).get("value"),
            "service_date": detail.get("ServiceDate"),
        })
    return items


def create_sync_run(watermark_from):
    result = supabase.table("sync_runs").insert({
        "status": "running",
        "environment": ENVIRONMENT,
        "triggered_by": "manual",
        "watermark_from": watermark_from
    }).execute()
    return result.data[0]["run_id"]


def complete_sync_run(run_id, records_fetched, records_upserted,
                      records_failed, pages_fetched, qb_api_calls,
                      duration_ms, watermark_to, error_message=None):
    status = "success" if not error_message else "failed"
    supabase.table("sync_runs").update({
        "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "records_fetched": records_fetched,
        "records_upserted": records_upserted,
        "records_failed": records_failed,
        "pages_fetched": pages_fetched,
        "qb_api_calls": qb_api_calls,
        "duration_ms": duration_ms,
        "watermark_to": watermark_to,
        "error_message": error_message
    }).eq("run_id", run_id).execute()


def sync_invoices():
    start_time = time.time()

    # Get current watermark
    watermark = get_watermark()
    print(f"Watermark: {watermark}")

    run_id = create_sync_run(watermark_from=watermark)
    print(f"Sync run started: {run_id}")

    records_fetched = 0
    records_upserted = 0
    records_failed = 0
    sync_to = datetime.now(timezone.utc).isoformat()

    try:
        token = refresh_access_token()
        invoices = fetch_invoices(token, since=watermark)
        records_fetched = len(invoices)

        if records_fetched == 0:
            print("No changes since last sync — nothing to do.")
        else:
            invoice_records = [transform_invoice(inv) for inv in invoices]
            line_item_records = []
            seen = set()
            for inv in invoices:
                for item in transform_line_items(inv):
                    key = (item["invoice_id"], item["line_number"])
                    if key not in seen:
                        seen.add(key)
                        line_item_records.append(item)
            supabase.table("invoices").upsert(invoice_records).execute()
            supabase.table("invoice_line_items").upsert(
                line_item_records,
                on_conflict="invoice_id,line_number"
            ).execute()

            records_upserted = records_fetched
            print(f"Synced {records_fetched} invoices")
            print(f"Synced {len(line_item_records)} line items")

        # Advance watermark only on success
        update_watermark(sync_to)

        duration_ms = int((time.time() - start_time) * 1000)
        complete_sync_run(
            run_id=run_id,
            records_fetched=records_fetched,
            records_upserted=records_upserted,
            records_failed=records_failed,
            pages_fetched=1,
            qb_api_calls=2,
            duration_ms=duration_ms,
            watermark_to=sync_to
        )

        print(f"Duration: {duration_ms}ms")
        validate()
        print(f"Sync complete.")

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        complete_sync_run(
            run_id=run_id,
            records_fetched=records_fetched,
            records_upserted=records_upserted,
            records_failed=records_failed,
            pages_fetched=0,
            qb_api_calls=0,
            duration_ms=duration_ms,
            watermark_to=None,
            error_message=str(e)
        )
        print(f"Sync failed: {e}")
        raise


if __name__ == "__main__":
    sync_invoices()