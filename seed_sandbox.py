"""
seed_sandbox.py - makes REAL PayPal *sandbox* payments for the top flagged demo
transactions, so the refund button in the demo moves (test) money for real.

Why this is needed: a refund needs a captured payment, and a capture needs a buyer
to approve the order. Two steps:

  1) python seed_sandbox.py create 3
       -> creates 3 sandbox orders (amounts match the top flagged rows) and prints
          approval links. Open each link, log in with a SANDBOX PERSONAL (buyer)
          account from developer.paypal.com > Testing Tools > Sandbox Accounts,
          and approve the payment.
  2) python seed_sandbox.py capture
       -> captures the approved orders and writes data/captures.json
          (safe to run more than once: already-captured orders are reused)

Each run of `create` starts clean: old captures/refunds are ignored so a fresh
demo always uses fresh orders (a capture can only be refunded once).

Disclosure for the pitch: the sandbox payments are linked to demo rows; the
transaction features come from the public dataset.
"""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import paypal_client as pp  # noqa: E402
from core import TOP_N, Pipeline  # noqa: E402
from verifier import REFUND_MIN_RISK, REFUND_MIN_SIM  # noqa: E402

ORDERS = "data/orders_pending.json"
CAPTURES = "data/captures.json"
REFUNDS = "data/refunds.json"


def create(n: int):
    pipe = Pipeline()
    # Start clean: stale captures/refunds from earlier runs must not leak in.
    for path in (CAPTURES, REFUNDS):
        if os.path.exists(path):
            os.remove(path)
            print(f"Removed old {path}")
    existing: dict = {}
    picked = [f for f in pipe.flags(TOP_N)
              if f["amount"] >= 1.0 and f["tx_id"] not in existing
              and f["risk_index"] >= REFUND_MIN_RISK
              and f["similar_confirmed_frauds"][0]["similarity"] >= REFUND_MIN_SIM][:n]
    if not picked:
        sys.exit("No flagged rows with amount >= $1 found.")
    if len(picked) < n:
        print(f"Note: only {len(picked)} of the top {TOP_N} flagged rows meet the refund rules; creating {len(picked)}.")
    pending = {}
    for f in picked:
        order = pp.create_order(f["amount"], f"GhostRing demo {f['tx_id']}")
        pending[f["tx_id"]] = {"order_id": order["id"], "amount": f["amount"]}
        print(f"\n{f['tx_id']}  ${f['amount']:.2f}  order {order['id']}")
        print("  approve here ->", pp.approve_link(order))
    json.dump(pending, open(ORDERS, "w"), indent=2)
    print(f"\nSaved {ORDERS}. After approving ALL links, run: python seed_sandbox.py capture")


def capture():
    pending = json.load(open(ORDERS))
    out = {}  # start fresh so stale captures never survive a re-seed
    for tx_id, o in pending.items():
        try:
            order = pp.get_order(o["order_id"])
            status = order.get("status")
            if status == "COMPLETED":      # already captured: reuse it
                res = order
            elif status == "APPROVED":     # buyer approved: capture now
                res = pp.capture_order(o["order_id"])
            else:
                print(tx_id, f"status={status}: buyer has not approved yet, skipping")
                continue
            cid = pp.capture_id_of(res)
            if not cid:
                print(tx_id, "-> no capture id in response:", str(res)[:200])
                continue
            out[tx_id] = {**o, "capture_id": cid}
            print(tx_id, "ready ->", cid)
        except Exception as e:
            print(tx_id, "FAILED:", e)
    json.dump(out, open(CAPTURES, "w"), indent=2)
    print(f"Wrote {CAPTURES} ({len(out)} linked payments)")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "create":
        create(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    elif len(sys.argv) >= 2 and sys.argv[1] == "capture":
        capture()
    else:
        print(__doc__)
