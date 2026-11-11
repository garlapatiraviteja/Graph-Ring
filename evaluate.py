"""
evaluate.py - Graph Ghost vs ordinary supervised baselines when only a few
fraud labels are known.

Usage:
    python evaluate.py --csv data/creditcard.csv
    python evaluate.py --csv data/creditcard.csv --fraud-labels 5 10 25 --seeds 5

Everything is measured on transactions whose labels were NOT given to any
method. Results are mean +- std over several random seeds.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from graph_ghost import GraphGhost


def load(csv: str, n_legit: int, n_fraud: int | None, seed: int):
    df = pd.read_csv(csv)
    feats = [c for c in df.columns if c.startswith("V")] + ["Amount"]
    rng = np.random.default_rng(seed)
    fraud = df[df["Class"] == 1]
    legit = df[df["Class"] == 0]
    if n_fraud is not None and n_fraud < len(fraud):
        fraud = fraud.sample(n_fraud, random_state=seed)
    legit = legit.sample(min(n_legit, len(legit)), random_state=seed)
    sub = pd.concat([fraud, legit]).sample(frac=1, random_state=seed).reset_index(drop=True)
    return sub[feats].to_numpy(dtype=np.float32), sub["Class"].to_numpy(dtype=int)


def pick_labels(y, n_fraud_labels, n_legit_labels, rng):
    f = rng.choice(np.where(y == 1)[0], n_fraud_labels, replace=False)
    l = rng.choice(np.where(y == 0)[0], n_legit_labels, replace=False)
    idx = np.concatenate([f, l])
    return idx, y[idx]


def recall_at_top(y_true, score, frac):
    k = max(1, int(len(score) * frac))
    top = np.argsort(-score)[:k]
    return y_true[top].sum() / max(1, y_true.sum())


def metrics(y_true, score):
    return {
        "PR-AUC": average_precision_score(y_true, score),
        "ROC-AUC": roc_auc_score(y_true, score),
        "Recall@top1%": recall_at_top(y_true, score, 0.01),
    }


def run_once(X, y, n_fraud_labels, n_legit_labels, seed, k):
    rng = np.random.default_rng(seed)
    idx, lab = pick_labels(y, n_fraud_labels, n_legit_labels, rng)
    mask = np.ones(len(y), dtype=bool)
    mask[idx] = False  # evaluate only on unlabeled rows
    out = {}

    t = time.time()
    s = GraphGhost(k=k).score(X, idx, lab)
    out["GraphGhost"] = {**metrics(y[mask], s[mask]), "sec": time.time() - t}

    Xs = StandardScaler().fit_transform(X)
    for name, model in {
        "LogReg": LogisticRegression(max_iter=1000, class_weight="balanced"),
        "RandomForest": RandomForestClassifier(
            n_estimators=200, class_weight="balanced", n_jobs=-1, random_state=seed
        ),
    }.items():
        t = time.time()
        model.fit(Xs[idx], lab)
        s = model.predict_proba(Xs)[:, 1]
        out[name] = {**metrics(y[mask], s[mask]), "sec": time.time() - t}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--n-legit", type=int, default=30000)
    ap.add_argument("--n-fraud", type=int, default=None, help="default: all 492")
    ap.add_argument("--fraud-labels", type=int, nargs="+", default=[5, 10, 25])
    ap.add_argument("--legit-labels", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--out", default="results.json")
    a = ap.parse_args()

    results = {}
    for nfl in a.fraud_labels:
        runs = []
        for seed in range(a.seeds):
            X, y = load(a.csv, a.n_legit, a.n_fraud, seed)
            if seed == 0 and nfl == a.fraud_labels[0]:
                print(f"Sample: {len(y)} rows, {y.sum()} fraud ({y.mean()*100:.2f}%)")
            runs.append(run_once(X, y, nfl, a.legit_labels, seed, a.k))
        results[nfl] = runs

        print(f"\n=== {nfl} labeled fraud + {a.legit_labels} labeled legit "
              f"(mean +- std over {a.seeds} seeds) ===")
        for method in runs[0]:
            row = []
            for m in ["PR-AUC", "ROC-AUC", "Recall@top1%"]:
                v = np.array([r[method][m] for r in runs])
                row.append(f"{m} {v.mean():.3f}+-{v.std():.3f}")
            print(f"{method:13s} " + " | ".join(row))

    with open(a.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved raw results to {a.out}")


if __name__ == "__main__":
    main()
