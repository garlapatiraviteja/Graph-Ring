"""app.py - GhostRing API + tiny web page.  Run: uvicorn app:app --reload"""
from __future__ import annotations

import json
import os
import threading

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import llm  # noqa: E402
import paypal_client  # noqa: E402
from core import TOP_N, Pipeline  # noqa: E402
from verifier import decide, verify_text  # noqa: E402

app = FastAPI(title="GhostRing")
pipe = Pipeline()
FLAGGED = set(pipe.new_ids[:TOP_N])   # only these can be explained (caps LLM spend)

CAPTURES_PATH = os.getenv("CAPTURES_PATH", "data/captures.json")
captures: dict = json.load(open(CAPTURES_PATH)) if os.path.exists(CAPTURES_PATH) else {}

_cache: dict[str, dict] = {}   # tx_id -> verified proposal (also caps LLM calls)
REFUNDS_PATH = os.getenv("REFUNDS_PATH", "data/refunds.json")
_done: dict[str, dict] = json.load(open(REFUNDS_PATH)) if os.path.exists(REFUNDS_PATH) else {}  # tx_id -> receipt
_lock = threading.Lock()


def _save_done():
    try:
        with open(REFUNDS_PATH, "w") as fh:
            json.dump(_done, fh, indent=2)
    except OSError:
        pass  # read-only disk: receipts stay in memory only


def _live_capture(cap: dict) -> dict:
    """PayPal's own record of the payment. Never raises."""
    try:
        d = paypal_client.get_capture(cap["capture_id"])
        amt = d.get("amount") or {}
        return {"status": d.get("status"), "amount": amt.get("value"), "currency": amt.get("currency_code")}
    except Exception as e:
        return {"status": "UNKNOWN", "error": str(e)[:120]}


def _mark_refunded_by_paypal(tx_id: str, cap: dict):
    with _lock:
        if tx_id not in _done:
            _done[tx_id] = {"refund_id": None, "status": "ALREADY_REFUNDED",
                            "amount": cap["amount"], "capture_id": cap["capture_id"]}
            _save_done()


def _sync_with_paypal():
    """On startup, learn which linked payments PayPal already shows as refunded
    (the server may have restarted since a judge refunded one)."""
    if not paypal_client.CLIENT_ID:
        return
    for tx_id, cap in captures.items():
        if tx_id not in _done and _live_capture(cap).get("status") == "REFUNDED":
            _mark_refunded_by_paypal(tx_id, cap)


_sync_with_paypal()


def build_proposal(tx_id: str) -> dict:
    facts = pipe.facts(tx_id)
    raw, error = None, None
    try:
        raw = llm.explain(facts)
    except Exception as e:  # network, quota, bad JSON ...
        error = f"{type(e).__name__}: {e}"

    verified, problems, fallback_used = False, [], False
    if raw is not None:
        verified, problems = verify_text(raw["explanation"], facts)
    if raw is None or not verified:
        fallback_used = True
        mock = llm.mock_explain(facts)
        explanation, proposed, source = mock["explanation"], mock["action"], "template (LLM output rejected)"
        if raw is None:
            source = "template (LLM unavailable)"
    else:
        explanation, proposed, source = raw["explanation"], raw["action"], raw["source"]

    near = facts["similar_confirmed_frauds"]
    action, note = decide(proposed, facts["risk_index"], near[0]["similarity"] if near else None)

    paypal_capture = None
    if tx_id in captures:
        paypal_capture = _live_capture(captures[tx_id])
        if paypal_capture.get("status") == "REFUNDED":
            _mark_refunded_by_paypal(tx_id, captures[tx_id])
    return {
        "facts": facts,
        "explanation": explanation,
        "llm_proposed_action": proposed,
        "final_action": action,
        "action_note": note,
        "source": source,
        "verifier": {"passed": verified, "problems": problems, "fallback_used": fallback_used},
        "llm_error": error,
        "paypal_linked": tx_id in captures,
        "paypal_capture": paypal_capture,
        "refunded": _done.get(tx_id),
    }


@app.get("/api/flags")
def flags():
    return {"currency": paypal_client.CURRENCY,
            "flags": [{**f, "paypal_linked": f["tx_id"] in captures,
                       "refunded": f["tx_id"] in _done} for f in pipe.flags()]}


@app.get("/api/backtest")
def backtest():
    return pipe.backtest()


@app.post("/api/explain/{tx_id}")
def explain(tx_id: str):
    if tx_id not in FLAGGED:
        raise HTTPException(404, "unknown transaction")
    if tx_id not in _cache:                      # LLM call happens OUTSIDE the lock,
        prop = build_proposal(tx_id)             # so one slow call can't block refunds
        with _lock:
            _cache.setdefault(tx_id, prop)
    out = dict(_cache[tx_id])
    if tx_id in captures:                        # always show PayPal's current record, not the first lookup
        live = _live_capture(captures[tx_id])
        out["paypal_capture"] = live
        if live.get("status") == "REFUNDED":
            _mark_refunded_by_paypal(tx_id, captures[tx_id])
    out["refunded"] = _done.get(tx_id)
    return out


@app.post("/api/refund/{tx_id}")
def refund(tx_id: str):
    """Executes a PayPal sandbox refund, only if the CODE-verified decision is 'refund'."""
    prop = _cache.get(tx_id)
    if not prop:
        raise HTTPException(409, "Explain the transaction first.")
    if prop["final_action"] != "refund":
        raise HTTPException(409, f"Decision is '{prop['final_action']}', not refund.")
    cap = captures.get(tx_id)
    if not cap:
        raise HTTPException(409, "No sandbox payment is linked to this transaction (demo seed).")
    with _lock:
        if tx_id in _done:
            return _done[tx_id]

    # Preflight: ask PayPal what it has on record before any money moves.
    live = _live_capture(cap)
    status = live.get("status")
    if status == "REFUNDED":
        _mark_refunded_by_paypal(tx_id, cap)
        return _done[tx_id]
    if status == "UNKNOWN":                      # fail closed: no PayPal confirmation, no refund
        raise HTTPException(409, "Could not confirm this payment with PayPal right now; refund blocked. Try again.")
    if status != "COMPLETED":
        raise HTTPException(409, f"PayPal reports this payment as {status}; refund blocked.")
    if live.get("amount") and abs(float(live["amount"]) - float(cap["amount"])) > 0.005:
        raise HTTPException(409, f"Amount mismatch: PayPal has {live['amount']}, expected {cap['amount']}.")

    with _lock:
        if tx_id in _done:
            return _done[tx_id]
        try:
            res = paypal_client.refund_capture(
                cap["capture_id"], cap["amount"],
                "Refund issued after fraud-risk review",
                # unique per capture, so a re-seeded tx_id never reuses an old key
                request_id=f"ghostring-{tx_id}-{cap['capture_id']}",
            )
        except Exception as e:
            msg = str(e)
            if "CAPTURE_FULLY_REFUNDED" in msg:
                _done[tx_id] = {"refund_id": None, "status": "ALREADY_REFUNDED",
                                "amount": cap["amount"], "capture_id": cap["capture_id"]}
                _save_done()
                return _done[tx_id]
            if "REFUND_NOT_ALLOWED" in msg:
                raise HTTPException(409, "PayPal refused this refund: the payment cannot be refunded again.")
            raise HTTPException(502, msg)
        _done[tx_id] = {"refund_id": res.get("id"), "status": res.get("status"),
                        "amount": cap["amount"], "capture_id": cap["capture_id"]}
        _save_done()
        return _done[tx_id]


@app.get("/api/health")
def health():
    return {"llm_mode": llm.LLM_MODE, "paypal_configured": bool(paypal_client.CLIENT_ID),
            "linked_payments": len(captures), "currency": paypal_client.CURRENCY}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
