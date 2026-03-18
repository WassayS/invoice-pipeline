import os
import hmac
import hashlib
import json
import base64
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from pipeline.sync import sync_invoices

load_dotenv()

WEBHOOK_VERIFIER_TOKEN = os.getenv("QB_WEBHOOK_VERIFIER_TOKEN")

app = FastAPI()


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    QuickBooks signs payload with HMAC-SHA256 and encodes 
    the result as base64 — not hex. Must decode before comparing.
    """
    mac = hmac.new(
        key=WEBHOOK_VERIFIER_TOKEN.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256
    )
    expected = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook/quickbooks")
async def quickbooks_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    payload = await request.body()
    signature = request.headers.get("intuit-signature", "")

    # Verify signature — reject anything that doesn't match
    if not verify_webhook_signature(payload, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse the event
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Check if any invoice entities changed
    entities = []
    for notification in event.get("eventNotifications", []):
        for entity in notification.get("dataChangeEvent", {}).get("entities", []):
            entities.append(entity.get("name"))

    if "Invoice" in entities:
        print(f"Invoice change detected — triggering sync")
        background_tasks.add_task(sync_invoices)
    else:
        print(f"Non-invoice event received — ignoring")

    # QuickBooks requires 200 response within 3 seconds
    # We return immediately and run sync in background
    return {"status": "received"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)