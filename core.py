"""core.py - loads demo transactions, runs Graph Ghost, builds evidence + backtest."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from graph_ghost import GraphGhost
from verifier import REFUND_MIN_RISK, REFUND_MIN_SIM

DATA_PATH = os.getenv("DEMO_DATA", "data/demo_tx.csv")
TOP_N = int(os.getenv("TOP_N", "15"))


class Pipeline:
    def __init__(self, path: str = DATA_PATH):
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Run: python prepare_demo_data.py --csv data/creditcard.csv"
            )
        self.df = pd.read_csv(path)
        feats = [c for c in self.df.columns if c.startswith("V")] + ["Amount"]
        X = self.df[feats].to_numpy(dtype=np.float32)

        known = self.df["known"].to_numpy() == 1
        self.labeled_idx = np.where(known)[0]
        labels = self.df["is_fraud"].to_numpy()[self.labeled_idx]
        self.n_confirmed_fraud = int(labels.sum())

        raw = GraphGhost(k=10).score(X, self.labeled_idx, labels)
        self.df["score"] = raw
        top = raw[~known].max()
        self.df["risk"] = np.round(100 * raw / (top + 1e-12), 1).clip(0, 100)

        # evidence: most similar CONFIRMED frauds (cosine on standardised features)
        Z = StandardScaler().fit_transform(X)
        Z /= np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9
        fraud_idx = self.labeled_idx[labels == 1]
        self._sim = Z @ Z[fraud_idx].T
        self._fraud_idx = fraud_idx

        new = self.df[~known].sort_values("score", ascending=False)
        self.new_ids = list(new.tx_id)
        self.rank = {t: i + 1 for i, t in enumerate(self.new_ids)}
        self.by_id = {r.tx_id: i for i, r in enumerate(self.df.itertuples())}

    def facts(self, tx_id: str) -> dict:
        i = self.by_id[tx_id]
        r = self.df.iloc[i]
        sims = self._sim[i]
        order = np.argsort(-sims)[:3]
        neighbors = [
            {
                "tx_id": self.df.iloc[self._fraud_idx[j]].tx_id,
                "amount": round(float(self.df.iloc[self._fraud_idx[j]].Amount), 2),
                "similarity": round(float(sims[j]), 2),
            }
            for j in order
        ]
        return {
            "tx_id": tx_id,
            "amount": round(float(r.Amount), 2),
            "risk_index": float(r.risk),
            "rank": self.rank[tx_id],
            "n_new_transactions": len(self.new_ids),
            "n_confirmed_frauds": self.n_confirmed_fraud,
            "refund_min_risk": REFUND_MIN_RISK,
            "refund_min_similarity": REFUND_MIN_SIM,
            "similar_confirmed_frauds": neighbors,
        }

    def flags(self, n: int = TOP_N) -> list[dict]:
        return [self.facts(t) for t in self.new_ids[:n]]

    def backtest(self) -> dict:
        """Measured against the hidden truth. This is the only place is_fraud is used."""
        truth = self.df.set_index("tx_id")["is_fraud"]
        out = {"n_new": len(self.new_ids), "frauds_in_new": int(truth[self.new_ids].sum())}
        for k in (10, 15, 20):
            hit = int(truth[self.new_ids[:k]].sum())
            out[f"top{k}"] = {"flagged": k, "actual_fraud": hit, "precision": round(hit / k, 2)}
        out["base_rate"] = round(out["frauds_in_new"] / out["n_new"], 3)
        return out
