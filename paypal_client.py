"""paypal_client.py - thin PayPal REST client. SANDBOX ONLY (refuses anything else)."""
from __future__ import annotations

import os
import time
import uuid
from urllib.parse import urlparse

import requests

BASE = os.getenv("PAYPAL_BASE", "https://api-m.sandbox.paypal.com")
CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "").strip().strip("\"'")
SECRET = os.getenv("PAYPAL_SECRET", "").strip().strip("\"'")
CURRENCY = os.getenv("PAYPAL_CURRENCY", "USD").strip().upper()

_u = urlparse(BASE)
if _u.scheme != "https" or not (_u.hostname or "").endswith(".sandbox.paypal.com"):
    raise RuntimeError("Refusing to run: PAYPAL_BASE must be an https *.sandbox.paypal.com URL.")

_token = {"value": None, "exp": 0.0}


def _access_token() -> str:
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    if not CLIENT_ID or not SECRET:
        raise RuntimeError("PAYPAL_CLIENT_ID / PAYPAL_SECRET are not set")
    r = requests.post(
        f"{BASE}/v1/oauth2/token",
        auth=(CLIENT_ID, SECRET),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    _token["value"], _token["exp"] = d["access_token"], time.time() + int(d.get("expires_in", 300))
    return _token["value"]


def _headers(idempotency_key: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"}
    if idempotency_key:
        h["PayPal-Request-Id"] = idempotency_key
    return h


def _check(r: requests.Response) -> dict:
    if not r.ok:
        raise RuntimeError(f"PayPal {r.status_code}: {r.text[:400]}")
    return r.json() if r.text else {}


def create_order(amount: float, description: str) -> dict:
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {"description": description[:127],
             "amount": {"currency_code": CURRENCY, "value": f"{amount:.2f}"}}
        ],
        "application_context": {
            "return_url": "https://example.com/return",
            "cancel_url": "https://example.com/cancel",
            "user_action": "PAY_NOW",
        },
    }
    return _check(requests.post(f"{BASE}/v2/checkout/orders", json=body,
                                headers=_headers(str(uuid.uuid4())), timeout=30))


def approve_link(order: dict) -> str | None:
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link["href"]
    return None


def get_order(order_id: str) -> dict:
    """Current state of an order (CREATED / APPROVED / COMPLETED ...)."""
    return _check(requests.get(f"{BASE}/v2/checkout/orders/{order_id}",
                               headers=_headers(), timeout=30))


def get_capture(capture_id: str) -> dict:
    """Current state of a capture (COMPLETED / REFUNDED / PARTIALLY_REFUNDED ...)."""
    return _check(requests.get(f"{BASE}/v2/payments/captures/{capture_id}",
                               headers=_headers(), timeout=30))


def capture_order(order_id: str) -> dict:
    return _check(requests.post(f"{BASE}/v2/checkout/orders/{order_id}/capture",
                                headers=_headers(str(uuid.uuid4())), json={}, timeout=30))


def capture_id_of(captured: dict) -> str | None:
    try:
        return captured["purchase_units"][0]["payments"]["captures"][0]["id"]
    except (KeyError, IndexError):
        return None


def refund_capture(capture_id: str, amount: float, note: str, request_id: str) -> dict:
    """Full or partial refund of a captured payment. request_id makes it idempotent."""
    body = {
        "amount": {"currency_code": CURRENCY, "value": f"{amount:.2f}"},
        "note_to_payer": note[:255],
    }
    return _check(requests.post(f"{BASE}/v2/payments/captures/{capture_id}/refund",
                                json=body, headers=_headers(request_id), timeout=30))
