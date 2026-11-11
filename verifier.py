"""
verifier.py - plain code that checks the LLM. No model involved.

Rules:
  1. Every number in the explanation must equal a number from the facts the
     solver produced (amount, risk index, rank, similarity, counts).
  2. Every transaction ID mentioned must be one we supplied.
  3. The proposed action must be one of the allowed actions.
  4. The final action is decided by CODE: the LLM can only ask for a refund,
     and the refund is downgraded to 'review' if the risk is below threshold.
"""
from __future__ import annotations

import os
import re

ALLOWED_ACTIONS = {"refund", "review", "approve"}
REFUND_MIN_RISK = float(os.getenv("REFUND_MIN_RISK", "60"))
REFUND_MIN_SIM = float(os.getenv("REFUND_MIN_SIM", "0.70"))

_ID = re.compile(r"\bTX-\d+\b")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _flatten(obj, out: list[float]):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
        if isinstance(obj, float) and 0 <= obj <= 1:
            out.append(obj * 100)  # allow "0.87" to be written as "87%"
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten(v, out)


def _allowed_ids(obj, out: set[str]):
    if isinstance(obj, str):
        out.update(_ID.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _allowed_ids(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _allowed_ids(v, out)


def verify_text(text: str, facts: dict) -> tuple[bool, list[str]]:
    """Returns (ok, problems)."""
    problems: list[str] = []

    ids_ok: set[str] = set()
    _allowed_ids(facts, ids_ok)
    for tid in set(_ID.findall(text)):
        if tid not in ids_ok:
            problems.append(f"unknown id {tid}")

    allowed: list[float] = []
    _flatten(facts, allowed)
    allowed.append(float(len(facts.get("similar_confirmed_frauds", []))))

    stripped = _ID.sub(" ", text)
    for tok in _NUM.findall(stripped):
        val = float(tok.replace(",", ""))
        if not any(abs(val - a) < 0.006 for a in allowed):
            problems.append(f"number {tok} not in data")
    return (not problems, problems)


def decide(proposed: str, risk: float, top_similarity: float | None = None) -> tuple[str, str | None]:
    """Code-enforced final action. Returns (action, note)."""
    proposed = (proposed or "").strip().lower()
    if proposed not in ALLOWED_ACTIONS:
        return "review", f"invalid action '{proposed}' replaced by review"
    if proposed == "refund":
        if risk < REFUND_MIN_RISK:
            return "review", f"refund downgraded: risk {risk:g} below threshold {REFUND_MIN_RISK:g}"
        if top_similarity is None or top_similarity < REFUND_MIN_SIM:
            shown = "n/a" if top_similarity is None else f"{top_similarity:g}"
            return "review", (f"refund downgraded: closest confirmed fraud similarity {shown} "
                              f"below {REFUND_MIN_SIM:g}")
    return proposed, None
