"""Shannon Retrieval Benchmark — SQuAD v2 + STS-B datasets.

Tests Shannon's semantic retrieval accuracy using well-known academic datasets.
Runs against a TEMPORARY database (never touches your real ~/.shannon).

Usage:
    python benchmarks/retrieval_benchmark.py [--squad-n 500] [--stsb-n 200] [--skip-squad] [--skip-stsb]

Datasets:
    - SQuAD v2 (Stanford, Rajpurkar et al.) — reading comprehension QA
      License: CC BY-SA 4.0 | Publisher: Stanford NLP
    - STS-B (Semantic Textual Similarity Benchmark) — sentence similarity pairs
      License: See original papers | Publisher: sentence-transformers (Nils Reimers)

Both datasets are widely-used academic benchmarks, no sensitive content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("SHANNON_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768

# ---------------------------------------------------------------------------
# Lightweight Shannon replica (isolated, no imports from shannon.*)
# We don't import the real Shannon to avoid touching ~/.shannon.
# ---------------------------------------------------------------------------

@dataclass
class MiniShannon:
    """Minimal Shannon store for benchmarking — isolated tmp directory."""

    db_path: Path
    embed_db_path: Path
    _conn: Optional[sqlite3.Connection] = field(default=None, repr=False)
    _econn: Optional[sqlite3.Connection] = field(default=None, repr=False)

    def init(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                content_hash TEXT PRIMARY KEY,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

        self._econn = sqlite3.connect(str(self.embed_db_path))
        self._econn.row_factory = sqlite3.Row
        self._econn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                content_hash TEXT PRIMARY KEY,
                embedding BLOB NOT NULL
            )
        """)
        self._econn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
        if self._econn:
            self._econn.close()

    def ingest(self, text: str) -> str:
        """Store text and compute embedding. Returns content hash."""
        content_hash = hashlib.sha256(text.encode()).hexdigest()

        self._conn.execute(
            "INSERT OR IGNORE INTO entries (content_hash, body, created_at) VALUES (?, ?, datetime('now'))",
            (content_hash, text),
        )
        self._conn.commit()

        # Compute embedding
        vec = self._embed(text)
        if vec:
            blob = struct.pack(f"!{len(vec)}f", *vec)
            self._econn.execute(
                "INSERT OR IGNORE INTO embeddings (content_hash, embedding) VALUES (?, ?)",
                (content_hash, blob),
            )
            self._econn.commit()

        return content_hash

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, str, float]]:
        """Semantic search. Returns [(content_hash, body, score), ...]."""
        query_vec = self._embed(query)
        if not query_vec:
            return []

        rows = self._conn.execute("SELECT content_hash, body FROM entries").fetchall()
        scored = []

        for row in rows:
            emb_row = self._econn.execute(
                "SELECT embedding FROM embeddings WHERE content_hash = ?",
                (row["content_hash"],),
            ).fetchone()
            if not emb_row:
                continue
            entry_vec = list(struct.unpack(f"!{EMBED_DIM}f", emb_row["embedding"]))
            sim = _cosine_similarity(query_vec, entry_vec)
            scored.append((row["content_hash"], row["body"], sim))

        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:top_k]

    def _embed(self, text: str) -> Optional[list[float]]:
        """Compute embedding via Ollama."""
        try:
            resp = httpx.post(
                f"{OLLAMA_URL}/api/embed",
                json={"model": EMBED_MODEL, "input": text[:8000]},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [])
            if embeddings:
                return embeddings[0]
        except Exception as e:
            print(f"  [WARN] Embedding failed: {e}", file=sys.stderr)
        return None

    def entry_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    def embedded_count(self) -> int:
        return self._econn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Benchmark: SQuAD v2
# ---------------------------------------------------------------------------

def run_squad_benchmark(shannon: MiniShannon, n_contexts: int = 500) -> dict:
    """
    Ingest SQuAD v2 context paragraphs as memories.
    For each question, check if Shannon retrieves the correct context in top-1 and top-5.

    Returns: {precision_at_1, precision_at_5, mrr, total_questions, avg_latency_ms}
    """
    from datasets import load_dataset

    print(f"\n{'='*60}")
    print(f"SQuAD v2 Retrieval Benchmark")
    print(f"{'='*60}")

    print(f"Loading dataset (first {n_contexts} unique contexts)...")
    ds = load_dataset("rajpurkar/squad_v2", split="validation")

    # Deduplicate contexts and take first N
    seen_contexts = {}
    qa_pairs = []

    for row in ds:
        ctx = row["context"]
        ctx_hash = hashlib.sha256(ctx.encode()).hexdigest()

        if ctx_hash not in seen_contexts and len(seen_contexts) < n_contexts:
            seen_contexts[ctx_hash] = ctx

        if ctx_hash in seen_contexts:
            question = row["question"]
            answers = row["answers"]["text"]
            if answers:  # Skip unanswerable questions
                qa_pairs.append({
                    "question": question,
                    "context_hash": ctx_hash,
                    "answer": answers[0],
                })

    print(f"Unique contexts: {len(seen_contexts)}")
    print(f"QA pairs (answerable): {len(qa_pairs)}")

    # Ingest contexts
    print(f"\nIngesting {len(seen_contexts)} contexts...")
    ingest_start = time.time()
    hash_map = {}  # shannon_hash -> ctx_hash

    for i, (ctx_hash, ctx_text) in enumerate(seen_contexts.items()):
        shannon_hash = shannon.ingest(ctx_text)
        hash_map[shannon_hash] = ctx_hash
        if (i + 1) % 50 == 0:
            elapsed = time.time() - ingest_start
            rate = (i + 1) / elapsed
            print(f"  Ingested {i+1}/{len(seen_contexts)} ({rate:.1f}/s)")

    ingest_time = time.time() - ingest_start
    print(f"  Done: {shannon.entry_count()} entries, {shannon.embedded_count()} embedded in {ingest_time:.1f}s")

    # Run queries (sample if too many)
    max_queries = min(len(qa_pairs), 200)
    import random
    random.seed(42)
    test_pairs = random.sample(qa_pairs, max_queries) if len(qa_pairs) > max_queries else qa_pairs

    print(f"\nRunning {len(test_pairs)} queries...")

    hits_at_1 = 0
    hits_at_5 = 0
    reciprocal_ranks = []
    latencies = []

    for i, pair in enumerate(test_pairs):
        t0 = time.time()
        results = shannon.search(pair["question"], top_k=5)
        latency_ms = (time.time() - t0) * 1000
        latencies.append(latency_ms)

        # Check if correct context is in results
        result_ctx_hashes = [hash_map.get(r[0]) for r in results]

        if pair["context_hash"] in result_ctx_hashes:
            rank = result_ctx_hashes.index(pair["context_hash"]) + 1
            reciprocal_ranks.append(1.0 / rank)
            if rank == 1:
                hits_at_1 += 1
            hits_at_5 += 1
        else:
            reciprocal_ranks.append(0.0)

        if (i + 1) % 50 == 0:
            p1 = hits_at_1 / (i + 1)
            p5 = hits_at_5 / (i + 1)
            avg_lat = sum(latencies) / len(latencies)
            print(f"  {i+1}/{len(test_pairs)}: P@1={p1:.3f} P@5={p5:.3f} avg_lat={avg_lat:.0f}ms")

    # Final metrics
    total = len(test_pairs)
    metrics = {
        "dataset": "SQuAD v2",
        "contexts_ingested": len(seen_contexts),
        "questions_tested": total,
        "precision_at_1": round(hits_at_1 / total, 4),
        "precision_at_5": round(hits_at_5 / total, 4),
        "mrr": round(sum(reciprocal_ranks) / total, 4),
        "avg_latency_ms": round(sum(latencies) / total, 1),
        "p50_latency_ms": round(sorted(latencies)[total // 2], 1),
        "p99_latency_ms": round(sorted(latencies)[int(total * 0.99)], 1),
        "ingest_time_s": round(ingest_time, 1),
        "embedding_model": EMBED_MODEL,
    }

    print(f"\n{'─'*40}")
    print(f"RESULTS: SQuAD v2")
    print(f"{'─'*40}")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    return metrics


# ---------------------------------------------------------------------------
# Benchmark: STS-B (Semantic Textual Similarity)
# ---------------------------------------------------------------------------

def run_stsb_benchmark(shannon: MiniShannon, n_pairs: int = 200) -> dict:
    """
    Test embedding quality using STS-B sentence pairs with human similarity scores.
    Computes Spearman correlation between Shannon's cosine similarity and human scores.

    Returns: {spearman_correlation, pearson_correlation, n_pairs, avg_cosine}
    """
    from datasets import load_dataset

    print(f"\n{'='*60}")
    print(f"STS-B Embedding Quality Benchmark")
    print(f"{'='*60}")

    print(f"Loading dataset (first {n_pairs} pairs)...")
    ds = load_dataset("sentence-transformers/stsb", split="test")

    pairs = []
    for i, row in enumerate(ds):
        if i >= n_pairs:
            break
        pairs.append({
            "sentence1": row["sentence1"],
            "sentence2": row["sentence2"],
            "score": row["score"],  # 0-1 normalized human similarity
        })

    print(f"Loaded {len(pairs)} sentence pairs")

    # Compute embeddings and cosine similarities
    print(f"\nComputing embeddings and similarities...")
    human_scores = []
    cosine_scores = []

    for i, pair in enumerate(pairs):
        vec1 = shannon._embed(pair["sentence1"])
        vec2 = shannon._embed(pair["sentence2"])

        if vec1 and vec2:
            cosine = _cosine_similarity(vec1, vec2)
            human_scores.append(pair["score"])
            cosine_scores.append(cosine)

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(pairs)} computed")

    # Compute correlations
    n = len(human_scores)
    if n < 10:
        print("  [ERROR] Too few valid pairs for correlation")
        return {"error": "insufficient data"}

    spearman = _spearman_correlation(human_scores, cosine_scores)
    pearson = _pearson_correlation(human_scores, cosine_scores)
    avg_cosine = sum(cosine_scores) / n
    avg_human = sum(human_scores) / n

    metrics = {
        "dataset": "STS-B",
        "pairs_tested": n,
        "spearman_correlation": round(spearman, 4),
        "pearson_correlation": round(pearson, 4),
        "avg_cosine_similarity": round(avg_cosine, 4),
        "avg_human_score": round(avg_human, 4),
        "embedding_model": EMBED_MODEL,
    }

    print(f"\n{'─'*40}")
    print(f"RESULTS: STS-B")
    print(f"{'─'*40}")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    # Interpretation
    if spearman > 0.80:
        print(f"\n  ✅ Excellent — embeddings align well with human similarity judgments")
    elif spearman > 0.65:
        print(f"\n  🟡 Good — embeddings capture most semantic similarity")
    else:
        print(f"\n  🔴 Weak — embeddings may not be suitable for semantic retrieval")

    return metrics


def _spearman_correlation(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation coefficient."""
    n = len(x)
    rank_x = _rank(x)
    rank_y = _rank(y)
    d_sq = sum((rx - ry) ** 2 for rx, ry in zip(rank_x, rank_y))
    return 1 - (6 * d_sq) / (n * (n * n - 1))


def _pearson_correlation(x: list[float], y: list[float]) -> float:
    """Pearson correlation coefficient."""
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    std_x = (sum((xi - mean_x) ** 2 for xi in x)) ** 0.5
    std_y = (sum((yi - mean_y) ** 2 for yi in y)) ** 0.5
    if std_x == 0 or std_y == 0:
        return 0.0
    return cov / (std_x * std_y)


def _rank(values: list[float]) -> list[float]:
    """Compute ranks (1-based, average ties)."""
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 1) / 2  # 1-based average
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Shannon Retrieval Benchmark")
    parser.add_argument("--squad-n", type=int, default=500, help="Number of SQuAD contexts to ingest")
    parser.add_argument("--stsb-n", type=int, default=200, help="Number of STS-B pairs to test")
    parser.add_argument("--skip-squad", action="store_true", help="Skip SQuAD benchmark")
    parser.add_argument("--skip-stsb", action="store_true", help="Skip STS-B benchmark")
    parser.add_argument("--output", type=str, default=None, help="Save results JSON to file")
    args = parser.parse_args()

    # Verify Ollama is running
    print("Checking Ollama...")
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        if EMBED_MODEL not in models and f"{EMBED_MODEL}:latest" not in models:
            print(f"  [ERROR] Embedding model '{EMBED_MODEL}' not found in Ollama. Available: {models}")
            sys.exit(1)
        print(f"  ✅ Ollama up, {EMBED_MODEL} available")
    except Exception as e:
        print(f"  [ERROR] Cannot reach Ollama at {OLLAMA_URL}: {e}")
        sys.exit(1)

    # Create temporary Shannon instance
    tmp_dir = Path(tempfile.mkdtemp(prefix="shannon-bench-"))
    print(f"\nUsing temporary DB: {tmp_dir}")

    shannon = MiniShannon(
        db_path=tmp_dir / "index.db",
        embed_db_path=tmp_dir / "embeddings.db",
    )
    shannon.init()

    results = {}

    try:
        if not args.skip_stsb:
            results["stsb"] = run_stsb_benchmark(shannon, n_pairs=args.stsb_n)

        if not args.skip_squad:
            results["squad"] = run_squad_benchmark(shannon, n_contexts=args.squad_n)

        # Summary
        print(f"\n{'='*60}")
        print(f"BENCHMARK COMPLETE")
        print(f"{'='*60}")
        print(json.dumps(results, indent=2))

        if args.output:
            out_path = Path(args.output)
            out_path.write_text(json.dumps(results, indent=2))
            print(f"\nResults saved to: {out_path}")

    finally:
        shannon.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"\nCleaned up temporary DB: {tmp_dir}")


if __name__ == "__main__":
    main()
