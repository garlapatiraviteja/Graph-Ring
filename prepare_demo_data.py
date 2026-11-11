"""
prepare_demo_data.py - build data/demo_tx.csv (a small, committable sample).

Usage:  python prepare_demo_data.py --csv data/creditcard.csv

The demo is a merchant's recent transactions:
  - 'known' rows: a few CONFIRMED frauds + known-good payments (the merchant's
    past chargebacks and settled orders). These are the only labels Graph Ghost sees.
  - 'new' rows: recent transactions that need a decision.
The hidden truth (is_fraud) is kept ONLY to measure how well the flags did.
"""
import argparse

import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--csv", required=True)
ap.add_argument("--n-new-legit", type=int, default=560)
ap.add_argument("--n-new-fraud", type=int, default=24)
ap.add_argument("--known-fraud", type=int, default=10)
ap.add_argument("--known-legit", type=int, default=200)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--out", default="data/demo_tx.csv")
a = ap.parse_args()

df = pd.read_csv(a.csv)
feats = [c for c in df.columns if c.startswith("V")] + ["Amount"]
fraud = df[df.Class == 1].sample(a.known_fraud + a.n_new_fraud, random_state=a.seed)
legit = df[df.Class == 0].sample(a.known_legit + a.n_new_legit, random_state=a.seed)

known = pd.concat([fraud.iloc[: a.known_fraud], legit.iloc[: a.known_legit]]).assign(known=1)
new = pd.concat([fraud.iloc[a.known_fraud :], legit.iloc[a.known_legit :]]).assign(known=0)
out = pd.concat([known, new]).sample(frac=1, random_state=a.seed).reset_index(drop=True)
out.insert(0, "tx_id", [f"TX-{i:04d}" for i in range(len(out))])
out = out.rename(columns={"Class": "is_fraud"})
out = out[["tx_id", "known", "is_fraud", "Amount"] + [c for c in feats if c != "Amount"]]
out.to_csv(a.out, index=False)
print(f"Wrote {a.out}: {len(out)} rows | known labels: {int(out.known.sum())} "
      f"| new rows: {int((out.known==0).sum())} ({int(out[out.known==0].is_fraud.sum())} fraud hidden)")
