"""llm.py - asks an LLM to explain a flag and propose an action. Output is UNTRUSTED
until verifier.py checks it. LLM_MODE=mock runs without any API key."""
from __future__ import annotations

import json
import os

import requests

from verifier import REFUND_MIN_RISK, REFUND_MIN_SIM

LLM_MODE = os.getenv("LLM_MODE", "gemini")  # "gemini" or "mock"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip().strip("\"'")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip("\"'")
_CUR = os.getenv("PAYPAL_CURRENCY", "USD").strip().upper()
_SYM = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}.get(_CUR, _CUR + " ")

SYSTEM = (
    "You help a small online merchant decide what to do with a suspicious payment. "
    "You are given FACTS computed by a fraud-scoring system. Write a plain-English "
    "explanation in at most 3 short sentences and propose one action: "
    "'refund', 'review' or 'approve'. "
    "STRICT RULES: use ONLY numbers and transaction IDs that appear in the FACTS. "
    "Never invent amounts, percentages, dates, names or counts. "
    "MERCHANT POLICY: propose 'refund' when risk_index is at least refund_min_risk AND "
    "the closest similar_confirmed_frauds similarity is 0.70 or higher; propose 'review' "
    "when risk_index is at least 25 but the refund conditions are not met; otherwise "
    "propose 'approve'. A human still confirms every refund. "
    "Do not claim certainty; the risk index is a ranking signal, not proof. "
    'Reply as JSON only: {"action": "...", "explanation": "..."}'
)


SYSTEM = SYSTEM.replace("0.70 or higher", f"{REFUND_MIN_SIM:.2f} or higher")


def mock_explain(facts: dict) -> dict:
    risk, amt = facts["risk_index"], facts["amount"]
    near = facts["similar_confirmed_frauds"][0]
    action = ("refund" if risk >= REFUND_MIN_RISK and near["similarity"] >= REFUND_MIN_SIM
              else "review" if risk >= 25 else "approve")
    text = (
        f"This {_SYM}{amt:.2f} payment ranks number {facts['rank']} of "
        f"{facts['n_new_transactions']} new transactions, with a risk index of {risk:g}. "
        f"Its closest match among {facts['n_confirmed_frauds']} confirmed frauds is "
        f"{near['tx_id']} (similarity {near['similarity']:.2f}). "
        f"Suggested action: {action}."
    )
    return {"action": action, "explanation": text, "source": "mock"}


def gemini_explain(facts: dict) -> dict:
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": "FACTS:\n" + json.dumps(facts)}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(text)
    return {
        "action": str(data.get("action", "")),
        "explanation": str(data.get("explanation", "")),
        "source": GEMINI_MODEL,
    }


def explain(facts: dict) -> dict:
    if LLM_MODE == "mock":
        return mock_explain(facts)
    return gemini_explain(facts)
