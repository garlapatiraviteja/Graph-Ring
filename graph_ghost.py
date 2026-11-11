"""
graph_ghost.py - few-label fraud scoring by label propagation on a kNN graph.

Idea: we only know a handful of confirmed fraud cases. Instead of training a
model, we build a similarity graph over all transactions and let the fraud
signal spread along the graph edges. No gradient descent, no training loop.

Adapted from the earlier Graph Ghost prototype (kNN graph with adaptive
bandwidth + normalized Laplacian). During the hackathon it was changed to:
  - iterative label spreading (scales to 50k+ rows; no dense/sparse solve)
  - binary fraud SCORE output (ranking), not argmax classes
  - class-mass normalisation so rare fraud is not drowned out
"""
from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix, diags
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler


def build_knn_graph(Z: np.ndarray, k: int = 10) -> csr_matrix:
    """Symmetric kNN affinity graph, cosine distance, adaptive bandwidth."""
    n = len(Z)
    k = min(k, n - 1)
    nn = NearestNeighbors(n_neighbors=k + 1, metric="cosine", n_jobs=-1).fit(Z)
    dist, idx = nn.kneighbors(Z)
    dist, idx = dist[:, 1:], idx[:, 1:]  # drop self

    # Zelnik-Manor local scale: each point's own neighbourhood size
    sigma = dist[:, max(0, k // 2 - 1)] + 1e-9
    rows = np.repeat(np.arange(n), k)
    cols = idx.ravel()
    w = np.exp(-(dist.ravel() ** 2) / (sigma[rows] * sigma[cols] + 1e-12))
    W = coo_matrix((w, (rows, cols)), shape=(n, n)).tocsr()
    W = W.maximum(W.T)
    W.setdiag(0.0)
    W.eliminate_zeros()
    return W


def normalized_adjacency(W: csr_matrix) -> csr_matrix:
    d = np.asarray(W.sum(axis=1)).ravel() + 1e-8
    Dmh = diags(1.0 / np.sqrt(d))
    return (Dmh @ W @ Dmh).tocsr()


def spread_scores(
    S: csr_matrix,
    labeled_idx: np.ndarray,
    labels: np.ndarray,
    alpha: float = 0.9,
    iters: int = 50,
) -> np.ndarray:
    """
    Label spreading (Zhou et al. 2004): F <- alpha * S F + (1 - alpha) * Y.
    Returns a fraud score per node (higher = more fraud-like).
    """
    n = S.shape[0]
    Y = np.zeros((n, 2), dtype=np.float64)
    Y[labeled_idx, labels.astype(int)] = 1.0
    F = Y.copy()
    for _ in range(iters):
        F = alpha * (S @ F) + (1 - alpha) * Y

    # class-mass normalisation: each class column sums to 1, so the rare
    # fraud class is comparable to the huge legit class
    F = F / (F.sum(axis=0, keepdims=True) + 1e-12)
    score = F[:, 1] / (F.sum(axis=1) + 1e-12)
    return score


class GraphGhost:
    """Fit-free scorer. Call .score(X, labeled_idx, labels) -> fraud scores."""

    def __init__(self, k: int = 10, alpha: float = 0.9, iters: int = 50):
        self.k, self.alpha, self.iters = k, alpha, iters
        self.graph_: csr_matrix | None = None

    def score(self, X: np.ndarray, labeled_idx: np.ndarray, labels: np.ndarray) -> np.ndarray:
        Z = StandardScaler().fit_transform(X).astype(np.float32)
        W = build_knn_graph(Z, self.k)
        self.graph_ = W
        S = normalized_adjacency(W)
        return spread_scores(S, labeled_idx, labels, self.alpha, self.iters)
