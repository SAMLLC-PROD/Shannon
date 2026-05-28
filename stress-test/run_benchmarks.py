#!/usr/bin/env python3
"""Shannon V2 Stress Test Benchmark Suite

Tests Shannon's capabilities across three phases:
  Phase 1: Foundation (embeddings, temporal decay, cross-domain, long-form, attribution, multilingual)
  Phase 2: Feature validation (#16 trust, #17 conflict, #18 distillation, #19 multi-pass)
  Phase 2.5: Spurious collision — proof that trust scoring suppresses bad entries
  Phase 3: Tenant isolation and concurrent load

Each test uses a uuid-based dedicated agent so test data never competes with
the 20K+ real entries in Shannon.
"""

import json
import time
import uuid
import hashlib
import random
import statistics
import sys
import os
import sqlite3 as _sqlite3
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict

SHANNON_URL = "http://localhost:8765"
RESULTS_DIR = Path(__file__).parent / "results"
DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# DB paths for direct manipulation
_DB_PATH = Path.home() / ".shannon" / "dictionary" / "layer_1" / "index.db"
_EMBED_DB_PATH = Path.home() / ".shannon" / "dictionary" / "layer_1" / "embeddings.db"

# Unique per-run tag appended to ALL saved bodies.
# Shannon is content-addressed: if a body already exists (from a prior run or real
# usage), INSERT is silently ignored and the new agent tag never attaches.
# Appending this tag makes every body unique so inserts always succeed.
_RUN_TAG = uuid.uuid4().hex[:8]

TEST_SESSION = f"stress-test-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


@dataclass
class BenchmarkResult:
    name: str
    phase: int
    capability: str
    metric: str
    value: float
    target: float
    passed: bool
    details: dict = field(default_factory=dict)
    duration_s: float = 0.0


ALL_RESULTS: list[BenchmarkResult] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def save_to_shannon(body: str, agent: str, tags: list = None, session_id: str = None):
    """Save an entry. Appends _RUN_TAG to body to guarantee a unique content hash
    even if the same text already exists in Shannon from a prior run or real usage."""
    tag_list = list(tags or [])
    if agent not in tag_list:
        tag_list = [agent] + tag_list
    payload = {
        "body": f"{body} [run:{_RUN_TAG}]",
        "agent": agent,
        "tags": tag_list,
        "session_id": session_id or TEST_SESSION,
    }
    r = requests.post(f"{SHANNON_URL}/memory", json=payload, timeout=10)
    return r.json()


def _get_embed_count() -> int:
    """Return current count of computed embeddings."""
    if not _EMBED_DB_PATH.exists():
        return 0
    conn = _sqlite3.connect(str(_EMBED_DB_PATH))
    count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    conn.close()
    return count


def _wait_for_embeddings(count_before: int, expected_new: int, timeout: int = 240) -> bool:
    """Block until ≥80% of expected_new embeddings appear in embeddings.db."""
    target = count_before + int(expected_new * 0.8)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(4)
        current = _get_embed_count()
        if current >= target:
            log(f"  Embeddings ready: {current - count_before} new computed")
            return True
    log(f"  ⚠️  Embedding timeout — proceeding with partial embeddings")
    return False


def _wait_for_hashes_embedded(content_hashes: list, timeout: int = 120) -> bool:
    """Block until ≥80% of the specific content_hashes have embeddings computed.

    More reliable than _wait_for_embeddings when other tests are also queuing
    embeddings — checks exactly the hashes we care about.
    """
    if not _EMBED_DB_PATH.exists():
        return False
    valid = [h for h in content_hashes if h]
    if not valid:
        return False
    target = int(len(valid) * 0.8)
    placeholders = ",".join("?" * len(valid))
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        conn = _sqlite3.connect(str(_EMBED_DB_PATH))
        count = conn.execute(
            f"SELECT COUNT(*) FROM embeddings WHERE content_hash IN ({placeholders})",
            valid,
        ).fetchone()[0]
        conn.close()
        if count >= target:
            log(f"  Embeddings ready: {count}/{len(valid)} specific hashes computed")
            return True
    log(f"  ⚠️  Embedding timeout for specific hashes — proceeding")
    return False


def query_shannon(topic: str, agent: str, limit_tokens: int = 4000):
    """Query Shannon for a topic scoped to agent."""
    r = requests.get(
        f"{SHANNON_URL}/memory",
        params={"agent": agent, "topic": topic, "limit_tokens": limit_tokens},
        timeout=30,
    )
    return r.json()


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _backdate_entries(content_hashes: list, hours_back: int = 336):
    """Set created_at to hours_back hours ago in the entries DB.

    Used by temporal decay test so old entries have genuinely low recency scores.
    336 hours = 14 days = 2 half-lives at the 168-hour decay rate.
    """
    if not _DB_PATH.exists():
        log(f"  ⚠️  DB not found at {_DB_PATH}, skipping backdate")
        return
    old_time = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
    conn = _sqlite3.connect(str(_DB_PATH))
    for ch in content_hashes:
        if ch:
            conn.execute(
                "UPDATE entries SET created_at = ? WHERE content_hash = ?",
                (old_time, ch),
            )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════
# PHASE 1: Foundation Tests
# ═══════════════════════════════════════════════════════════════════

def phase1_embedding_quality():
    """Test 1.1: Embedding quality using STS Benchmark."""
    log("Phase 1.1: Embedding Quality (STS Benchmark)")
    t0 = time.time()

    sts_agent = f"sts-{uuid.uuid4().hex[:6]}"

    from datasets import load_dataset
    sts = load_dataset("mteb/stsbenchmark-sts", split="test[:50]")

    log(f"  Loading {len(sts)} STS pairs into Shannon...")
    count_before = _get_embed_count()
    for i in range(len(sts)):
        save_to_shannon(
            body=sts[i]["sentence1"],
            agent=sts_agent,
            tags=["sts-benchmark", f"pair-{i}"],
            session_id=f"sts-{i}",
        )

    log(f"  Waiting for embeddings (up to 3 min)...")
    _wait_for_embeddings(count_before, len(sts), timeout=180)

    correct = 0
    total = 0
    for i in range(min(50, len(sts))):
        human_score = sts[i]["score"]
        results = query_shannon(sts[i]["sentence2"], agent=sts_agent, limit_tokens=2000)

        entries = results.get("entries", []) if isinstance(results, dict) else (results or [])

        found = False
        for entry in entries[:5]:
            body = entry.get("body", "") if isinstance(entry, dict) else str(entry)
            if sts[i]["sentence1"][:50] in body:
                found = True
                break

        if human_score >= 3.0:
            total += 1
            if found:
                correct += 1

    recall = correct / max(total, 1)
    duration = time.time() - t0

    result = BenchmarkResult(
        name="STS Embedding Quality",
        phase=1,
        capability="embedding_quality",
        metric="recall_high_similarity",
        value=round(recall, 3),
        target=0.75,
        passed=recall >= 0.75,
        details={"total_high_sim_pairs": total, "found": correct, "dataset_size": len(sts)},
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {recall:.1%} recall on high-similarity pairs (target ≥75%) — {'✅ PASS' if result.passed else '❌ FAIL'}")
    return result


def phase1_temporal_decay():
    """Test 1.2: Temporal decay — 'Updated:' entries outrank 14-day-old originals.

    Old entries are backdated 336 hours (2 half-lives) via direct SQLite write so
    their recency score is genuinely low (~0.25 vs 1.0 for new entries).
    Both old and new entries contain the same query keywords, ensuring the keyword
    pass doesn't artificially favour the old entry.  The newer entry wins via
    the combination of recency scoring and stable-sort DB ordering.
    """
    log("Phase 1.2: Temporal Decay (backdated old entries vs fresh 'Updated:' entries)")
    t0 = time.time()

    temporal_agent = f"temporal-{uuid.uuid4().hex[:6]}"

    topics = [
        (
            "Target AFR at WOT is 12.8:1",
            "Updated: target AFR at WOT revised to 12.5:1 after dyno testing",
        ),
        (
            "Ignition timing at 15 PSI is 18 degrees",
            "Updated: ignition timing at 15 PSI revised to 16 degrees for safety",
        ),
        (
            "Intercooler pressure drop is 1.5 PSI",
            "Updated: intercooler pressure drop measured at 2.1 PSI after testing",
        ),
        (
            "Wastegate spring rate is 10 PSI",
            "Updated: wastegate spring rate now 12 PSI after replacement",
        ),
        (
            "Fuel pump flow rate is 255 LPH",
            "Updated: fuel pump flow rate measured at 240 LPH with current setup",
        ),
        (
            "Boost target for daily driving is 14 PSI",
            "Updated: boost target for daily driving lowered to 12 PSI for reliability",
        ),
        (
            "Oil temperature limit is 250 degrees F",
            "Updated: oil temperature limit revised to 230 degrees F per specification",
        ),
        (
            "Coolant capacity is 12 quarts for this build",
            "Updated: coolant capacity is 13 quarts with aftermarket radiator installed",
        ),
        (
            "Battery voltage running should be 14.2 volts",
            "Updated: battery voltage running now 14.4 volts after alternator replaced",
        ),
        (
            "Tire pressure target is 35 PSI cold for track",
            "Updated: tire pressure target changed to 32 PSI cold for new compound",
        ),
    ]

    total_tests = len(topics)

    # Save old entries, capture hashes for backdating
    old_hashes = []
    for old_body, _ in topics:
        r = save_to_shannon(body=old_body, agent=temporal_agent, tags=["temporal-test"])
        old_hashes.append(r.get("id", ""))

    # Backdate old entries 14 days so recency ≈ 0.25
    _backdate_entries(old_hashes, hours_back=336)

    time.sleep(1)

    # Save new (Updated:) entries — created_at = now → recency ≈ 1.0
    for _, new_body in topics:
        save_to_shannon(body=new_body, agent=temporal_agent, tags=["temporal-test"])

    time.sleep(3)  # wait for embeddings

    newer_first = 0
    for old_body, new_body in topics:
        query_topic = old_body.split(" is ")[0] if " is " in old_body else old_body[:40]
        results = query_shannon(query_topic, agent=temporal_agent)
        entries = results.get("entries", []) if isinstance(results, dict) else (results or [])

        if entries:
            first_body = entries[0].get("body", "") if isinstance(entries[0], dict) else str(entries[0])
            update_kws = {"Updated", "revised", "Corrected", "Actually", "measured", "replaced", "lowered", "changed"}
            if any(kw in first_body for kw in update_kws):
                newer_first += 1

    rate = newer_first / total_tests
    duration = time.time() - t0

    result = BenchmarkResult(
        name="Temporal Decay",
        phase=1,
        capability="temporal_decay",
        metric="newer_entry_preferred_rate",
        value=round(rate, 3),
        target=0.85,
        passed=rate >= 0.85,
        details={"newer_first": newer_first, "total": total_tests},
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {rate:.1%} newer-preferred (target ≥85%) — {'✅ PASS' if result.passed else '❌ FAIL'}")
    return result


def phase1_cross_domain():
    """Test 1.3: Cross-domain retrieval across automotive, medical, and legal domains."""
    log("Phase 1.3: Cross-Domain Retrieval (synthetic multi-domain)")
    t0 = time.time()

    xdomain_agent = f"xdomain-{uuid.uuid4().hex[:6]}"

    domains = {
        "automotive": [
            "The optimal air-fuel ratio for a turbocharged engine at full boost is approximately 11.5:1 to 12.0:1",
            "Ignition timing should be retarded 2-3 degrees per pound of boost to prevent detonation",
            "Wastegate spring pressure determines base boost level before electronic control intervenes",
            "Intercooler efficiency drops significantly above 80% heat saturation, requiring larger core",
            "E85 fuel allows approximately 3-5 degrees more ignition advance due to higher octane rating",
        ],
        "medical": [
            "Type 2 diabetes is characterized by insulin resistance and progressive beta-cell dysfunction",
            "HbA1c levels above 6.5% are diagnostic for diabetes mellitus",
            "Metformin remains the first-line pharmacotherapy for type 2 diabetes management",
            "Diabetic retinopathy screening should occur annually after initial diagnosis",
            "GLP-1 receptor agonists show cardiovascular benefit beyond glycemic control",
        ],
        "legal": [
            "The Fourth Amendment protects against unreasonable searches and seizures by government",
            "Miranda rights must be read before custodial interrogation for statements to be admissible",
            "Strict liability applies in product liability cases regardless of manufacturer intent",
            "The statute of limitations for federal civil rights claims under Section 1983 is typically 2-3 years",
            "Attorney-client privilege is waived when the communication furthers a crime or fraud",
        ],
    }

    saved_hashes = []
    for domain, entries in domains.items():
        for entry in entries:
            r = save_to_shannon(
                body=entry,
                agent=xdomain_agent,
                tags=["cross-domain", domain],
                session_id=f"xdomain-{domain}",
            )
            saved_hashes.append(r.get("id", ""))

    _wait_for_hashes_embedded(saved_hashes, timeout=90)

    # Use explicit vocabulary matching the entry bodies so semantic search
    # unambiguously prefers the correct domain
    queries = {
        "automotive": "What is the optimal air-fuel ratio for a turbocharged engine at full boost?",
        "medical": "What pharmacotherapy is first-line for type 2 diabetes management?",
        "legal": "When must Miranda rights be read before custodial interrogation?",
    }

    domain_correct = 0
    domain_total = 0

    for domain, query in queries.items():
        results = query_shannon(query, agent=xdomain_agent)
        entries = results.get("entries", []) if isinstance(results, dict) else (results or [])

        for entry in entries[:3]:
            domain_total += 1
            body = entry.get("body", "") if isinstance(entry, dict) else str(entry)
            tags = entry.get("tags", []) if isinstance(entry, dict) else []
            if domain in str(tags) or any(kw in body.lower() for kw in domains[domain][0].split()[:3]):
                domain_correct += 1

    precision = domain_correct / max(domain_total, 1)
    duration = time.time() - t0

    result = BenchmarkResult(
        name="Cross-Domain Retrieval",
        phase=1,
        capability="cross_domain",
        metric="domain_precision_top3",
        value=round(precision, 3),
        target=0.80,
        passed=precision >= 0.80,
        details={"correct": domain_correct, "total": domain_total},
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {precision:.1%} domain precision (target ≥80%) — {'✅ PASS' if result.passed else '❌ FAIL'}")
    return result


def phase1_long_form():
    """Test 1.4: Long-form knowledge retrieval — specific facts within dense entries."""
    log("Phase 1.4: Long-Form Knowledge Retrieval")
    t0 = time.time()

    lf_agent = f"lf-{uuid.uuid4().hex[:6]}"

    long_entries = [
        {
            "body": (
                "The Dell PowerEdge R740xd is a 2U rack server designed for data-intensive workloads. "
                "It supports up to two Intel Xeon Scalable processors from the Skylake or Cascade Lake families. "
                "The maximum RAM capacity is 3TB using 128GB LRDIMMs across 24 DIMM slots. "
                "Storage options include up to 32 x 2.5-inch drives or 18 x 3.5-inch drives in various configurations. "
                "The integrated Dell Remote Access Controller (iDRAC9) provides full remote management including "
                "virtual console, virtual media, and lifecycle controller. The iDRAC Enterprise license adds "
                "features like group management, enhanced security, and automated updates. "
                "Network connectivity includes 2x 1GbE plus options for 10GbE, 25GbE, or 100GbE via NDC and add-in cards. "
                "Power supplies are hot-plug redundant, available in 495W, 750W, 1100W, or 1600W configurations. "
                "The PERC H740P RAID controller supports RAID 0, 1, 5, 6, 10, 50, and 60 with 8GB cache. "
                "Operating temperature range is 10°C to 35°C with expanded operating temperature support to 40°C. "
                "The system supports TPM 2.0, Secure Boot, and System Lockdown for security hardening."
            ),
            "tags": ["hardware", "server", "r740xd"],
            "query": "What is the maximum RAM for an R740xd?",
            "answer_fragment": "3TB",
        },
        {
            "body": (
                "Byzantine Fault Tolerance (BFT) is a consensus mechanism that allows distributed systems to "
                "function correctly even when some nodes are malicious or compromised. The classic result by "
                "Lamport, Shostak, and Pease shows that BFT requires at least 3f+1 nodes to tolerate f Byzantine "
                "faults. In practical BFT (PBFT) as described by Castro and Liskov, the protocol operates in "
                "three phases: pre-prepare, prepare, and commit. The pre-prepare phase has the primary propose "
                "a sequence number for the request. The prepare phase collects 2f matching prepare messages. "
                "The commit phase requires 2f+1 matching commit messages before execution. View changes handle "
                "primary failure by electing a new primary. The communication complexity is O(n^2) for each "
                "consensus round, which limits scalability. Modern variants like Tendermint and HotStuff reduce "
                "complexity to O(n) through pipelining and threshold signatures. Lattice Network uses a 5-of-7 "
                "BFT configuration with ML-DSA-87 post-quantum signatures, providing both Byzantine fault "
                "tolerance and quantum resistance simultaneously."
            ),
            "tags": ["consensus", "bft", "distributed-systems"],
            "query": "How many nodes does BFT need to tolerate f faults?",
            "answer_fragment": "3f+1",
        },
        {
            "body": (
                "Post-quantum cryptography addresses the threat that quantum computers pose to current "
                "public-key cryptographic systems. RSA and elliptic curve cryptography are vulnerable to "
                "Shor's algorithm running on a sufficiently large quantum computer. NIST standardized three "
                "post-quantum algorithms in 2024: ML-KEM (formerly CRYSTALS-Kyber) for key encapsulation, "
                "ML-DSA (formerly CRYSTALS-Dilithium) for digital signatures, and SLH-DSA (formerly SPHINCS+) "
                "as a stateless hash-based signature backup. ML-KEM-768 provides approximately 192-bit "
                "classical security. ML-DSA-87 provides approximately 256-bit classical security with "
                "signature sizes of about 4627 bytes. The migration timeline recommended by NIST suggests "
                "organizations begin transition by 2025 and complete it by 2030. Hybrid approaches combining "
                "classical and post-quantum algorithms are recommended during the transition period."
            ),
            "tags": ["cryptography", "pqc", "nist"],
            "query": "What is the signature size of ML-DSA-87?",
            "answer_fragment": "4627",
        },
    ]

    for entry in long_entries:
        save_to_shannon(
            body=entry["body"],
            agent=lf_agent,
            tags=entry["tags"],
            session_id="longform-test",
        )

    time.sleep(2)

    found = 0
    for entry in long_entries:
        results = query_shannon(entry["query"], agent=lf_agent, limit_tokens=8000)
        entries_list = results.get("entries", []) if isinstance(results, dict) else (results or [])

        for r in entries_list[:5]:
            body = r.get("body", "") if isinstance(r, dict) else str(r)
            if entry["answer_fragment"] in body:
                found += 1
                break

    recall = found / len(long_entries)
    duration = time.time() - t0

    result = BenchmarkResult(
        name="Long-Form Retrieval",
        phase=1,
        capability="long_form",
        metric="recall_at_5",
        value=round(recall, 3),
        target=0.50,
        passed=recall >= 0.50,
        details={"found": found, "total": len(long_entries)},
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {recall:.1%} recall@5 (target ≥50%) — {'✅ PASS' if result.passed else '❌ FAIL'}")
    return result


def phase1_source_attribution():
    """Test 1.5: Source attribution — correct entry surfaces for specific queries."""
    log("Phase 1.5: Source Attribution")
    t0 = time.time()

    sa_agent = f"sa-{uuid.uuid4().hex[:6]}"

    entries = [
        {"body": "Session 2026-03-15: Decided to use Cosmos SDK for native chain (M42)", "tags": ["decision", "m42"], "id": "src-001"},
        {"body": "Session 2026-02-28: Named the AI assistant 'Guy Shannon'", "tags": ["naming", "identity"], "id": "src-002"},
        {"body": "Session 2026-04-20: V-Index benchmarked at 95% accuracy, 30% improvement", "tags": ["benchmark", "vindex"], "id": "src-003"},
        {"body": "Session 2026-03-19: First user minted NFT on LatticeIdentity v3", "tags": ["milestone", "nft"], "id": "src-004"},
        {"body": "Session 2026-02-24: 7 validators deployed across 3 continents", "tags": ["deployment", "validators"], "id": "src-005"},
    ]

    for entry in entries:
        save_to_shannon(
            body=entry["body"],
            agent=sa_agent,
            tags=entry["tags"],
            session_id=entry["id"],
        )

    time.sleep(2)

    queries = [
        ("What was decided about the native chain framework?", "Cosmos SDK"),
        ("When was Guy Shannon named?", "2026-02-28"),
        ("What were the V-Index benchmark results?", "95%"),
        ("When was the first NFT minted?", "2026-03-19"),
        ("How many validators were deployed?", "7 validators"),
    ]

    correct = 0
    for query, expected_fragment in queries:
        results = query_shannon(query, agent=sa_agent)
        entries_list = results.get("entries", []) if isinstance(results, dict) else (results or [])

        if entries_list:
            top = entries_list[0]
            body = top.get("body", "") if isinstance(top, dict) else str(top)
            if expected_fragment in body:
                correct += 1

    accuracy = correct / len(queries)
    duration = time.time() - t0

    result = BenchmarkResult(
        name="Source Attribution",
        phase=1,
        capability="source_attribution",
        metric="top1_accuracy",
        value=round(accuracy, 3),
        target=0.80,
        passed=accuracy >= 0.80,
        details={"correct": correct, "total": len(queries)},
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {accuracy:.1%} top-1 accuracy (target ≥80%) — {'✅ PASS' if result.passed else '❌ FAIL'}")
    return result


def phase1_multilingual():
    """Test 1.6: Multilingual retrieval — informational baseline for nomic embeddings."""
    log("Phase 1.6: Multilingual Retrieval")
    t0 = time.time()

    ml_agent = f"ml-{uuid.uuid4().hex[:6]}"

    entries = [
        "The capital of France is Paris, known for the Eiffel Tower",
        "Water boils at 100 degrees Celsius at sea level atmospheric pressure",
        "The speed of light in vacuum is approximately 299,792,458 meters per second",
        "Photosynthesis converts carbon dioxide and water into glucose using sunlight",
        "The human heart pumps approximately 5 liters of blood per minute at rest",
    ]

    for entry in entries:
        save_to_shannon(body=entry, agent=ml_agent, tags=["multilingual-test"])

    time.sleep(2)

    multilingual_queries = {
        "Spanish": ("¿Cuál es la capital de Francia?", "Paris"),
        "French": ("À quelle température l'eau bout-elle?", "100 degrees"),
        "German": ("Wie schnell ist das Licht?", "299,792,458"),
        "Portuguese": ("O que é fotossíntese?", "Photosynthesis"),
        "Italian": ("Quanto sangue pompa il cuore umano?", "5 liters"),
    }

    found = 0
    details = {}
    for lang, (query, expected) in multilingual_queries.items():
        results = query_shannon(query, agent=ml_agent)
        entries_list = results.get("entries", []) if isinstance(results, dict) else (results or [])

        hit = False
        for r in entries_list[:5]:
            body = r.get("body", "") if isinstance(r, dict) else str(r)
            if expected in body:
                hit = True
                break
        details[lang] = hit
        if hit:
            found += 1

    recall = found / len(multilingual_queries)
    duration = time.time() - t0

    result = BenchmarkResult(
        name="Multilingual Retrieval",
        phase=1,
        capability="multilingual",
        metric="recall_at_5_cross_lingual",
        value=round(recall, 3),
        target=0.40,
        passed=True,  # Informational — measures capability, not a gate
        details={"found": found, "total": len(multilingual_queries), "per_language": details},
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {recall:.1%} cross-lingual recall (informational) — {json.dumps(details)}")
    return result


# ═══════════════════════════════════════════════════════════════════
# PHASE 2: Feature Validation (#16-#19)
# ═══════════════════════════════════════════════════════════════════

def phase2_multi_pass_retrieval():
    """Test 2.1: Multi-pass retrieval (#19) finds keyword-only matches.

    Creates an entry containing a made-up word ('zorblaxtest') that the embedding
    model cannot capture semantically.  The entry must be found via keyword pass
    and synthesis.passes_used must include 'keyword'.
    """
    log("Phase 2.1: Multi-Pass Retrieval — keyword pass finds unique term (#19)")
    t0 = time.time()

    mp_agent = f"mp-{uuid.uuid4().hex[:6]}"

    # 15 semantically unrelated decoy entries
    decoys = [
        "The human heart pumps about 5 liters of blood per minute at rest",
        "Miranda rights must be read before custodial interrogation for admissibility",
        "E85 ethanol fuel has approximately 105 octane rating compared to 91 premium",
        "HbA1c levels above 6.5 percent are diagnostic for type 2 diabetes mellitus",
        "The Fourth Amendment protects against unreasonable searches and seizures",
        "Metformin is the first-line pharmacotherapy for type 2 diabetes management",
        "Ignition timing retard prevents detonation under high boost conditions",
        "GLP-1 receptor agonists show cardiovascular benefit beyond glycemic control",
        "Battery voltage at idle should be between 12.4 and 12.7 volts at rest",
        "Attorney client privilege is waived when communication furthers a crime",
        "Wastegate actuator controls boost by bypassing exhaust flow from the turbine",
        "Diabetic retinopathy screening should occur annually after initial diagnosis",
        "Intercooler core should be sized for at least 90 percent efficiency at peak flow",
        "Strict liability applies in product cases regardless of manufacturer intent",
        "Coolant thermostat opens at approximately 195 degrees F on most modern engines",
    ]
    for decoy in decoys:
        save_to_shannon(body=decoy, agent=mp_agent, tags=["mp-decoy"])

    # Target entry with unique invented word the embedding model can't capture
    unique_kw = "zorblaxtest"
    target_body = (
        f"The {unique_kw} threshold parameter must be configured to 42 units "
        "for optimal system performance and reliability"
    )
    save_to_shannon(body=target_body, agent=mp_agent, tags=["mp-target"])

    time.sleep(3)

    results = query_shannon(
        f"What should the {unique_kw} parameter be configured to?",
        agent=mp_agent,
    )
    entries_list = results.get("entries", []) if isinstance(results, dict) else (results or [])
    synthesis = results.get("synthesis", {}) if isinstance(results, dict) else {}
    passes = synthesis.get("passes_used", [])

    found = any(
        unique_kw in (e.get("body", "") if isinstance(e, dict) else str(e))
        for e in entries_list[:5]
    )
    keyword_pass_active = "keyword" in passes
    passed = found and keyword_pass_active

    duration = time.time() - t0

    result = BenchmarkResult(
        name="Multi-Pass Retrieval",
        phase=2,
        capability="multi_pass",
        metric="keyword_target_found_and_pass_active",
        value=1.0 if passed else 0.0,
        target=1.0,
        passed=passed,
        details={
            "target_found": found,
            "keyword_pass_active": keyword_pass_active,
            "passes_used": passes,
            "unique_kw": unique_kw,
        },
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: found={found}, passes={passes} — {'✅ PASS' if passed else '❌ FAIL'}")
    return result


def phase2_conflict_detection():
    """Test 2.2: Conflict detection (#17) — contradictory entries produce 'conflicts' key.

    Injects 3 contradictory pairs (same meaningful tags, overlapping text, different
    numeric values).  Queries a topic that retrieves all 6 entries.  Passes if the
    response contains at least one detected conflict group.
    """
    log("Phase 2.2: Conflict Detection — contradictory entries flagged (#17)")
    t0 = time.time()

    cf_agent = f"cf-{uuid.uuid4().hex[:6]}"

    # Each pair: same meaningful tags, similar text, contradictory numbers
    contradictions = [
        (
            "Maximum safe boost pressure is 18 PSI for this engine build configuration",
            "Maximum safe boost pressure limit is 12 PSI for this engine build configuration",
            ["boost-config", "engine-specs"],
        ),
        (
            "Target battery voltage while running is 14.2 volts for this alternator setup",
            "Target battery voltage while running is 13.8 volts for this alternator setup",
            ["battery-specs", "electrical-system"],
        ),
        (
            "Maximum coolant temperature limit is 220 degrees before overheating alarm triggers",
            "Maximum coolant temperature limit is 195 degrees before overheating alarm triggers",
            ["coolant-system", "temperature-limits"],
        ),
    ]

    for body_a, body_b, extra_tags in contradictions:
        save_to_shannon(body=body_a, agent=cf_agent, tags=extra_tags)
        save_to_shannon(body=body_b, agent=cf_agent, tags=extra_tags)

    time.sleep(2)

    # Broad query that retrieves all 6 entries so conflict detection runs over all pairs
    results = query_shannon(
        "engine build specifications limits and targets",
        agent=cf_agent,
        limit_tokens=8000,
    )
    conflicts = results.get("conflicts", []) if isinstance(results, dict) else []
    conflict_count = len(conflicts)
    passed = conflict_count > 0

    duration = time.time() - t0

    result = BenchmarkResult(
        name="Conflict Detection",
        phase=2,
        capability="conflict_detection",
        metric="conflict_groups_detected",
        value=float(conflict_count),
        target=1.0,
        passed=passed,
        details={
            "conflict_groups": conflict_count,
            "conflicts": [
                {"group_id": c.get("conflict_group_id"), "entries": c.get("entry_ids", [])}
                for c in conflicts[:3]
            ],
        },
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {conflict_count} conflict groups detected (target ≥1) — {'✅ PASS' if passed else '❌ FAIL'}")
    return result


def phase2_expert_trust():
    """Test 2.3: Expert trust scoring (#16) — causal/verified entries outrank spurious.

    Loads 3 causal entries tagged ['causal-knowledge','verified'] and 3 spurious
    entries tagged ['spurious-correlation','no-causation'].  All entries are
    semantically relevant to the query.  The trust scoring formula gives causal
    entries weight 1.0 and spurious entries weight 0.1 (plus 0.3× penalty), so
    the top 3 results must all have trust_weight == 1.0.
    """
    log("Phase 2.3: Expert Trust Scoring — verified entries outrank spurious (#16)")
    t0 = time.time()

    tr_agent = f"tr-{uuid.uuid4().hex[:6]}"

    causal_entries = [
        (
            "Higher octane fuel prevents knock because its autoignition temperature is higher, "
            "enabling more ignition advance and measured power increase verified on dynamometer",
            ["causal-knowledge", "verified"],
        ),
        (
            "Intercooler reduces charge air temperature, lowering detonation risk and allowing "
            "more boost pressure — causal relationship confirmed by air temperature sensor data",
            ["causal-knowledge", "verified"],
        ),
        (
            "Timing advance increases peak cylinder pressure directly increasing torque output "
            "— causal effect on power verified through cylinder pressure transducer measurements",
            ["causal-knowledge", "verified"],
        ),
    ]

    spurious_entries = [
        (
            "Vehicles with aftermarket stickers correlate with higher dyno numbers in forums "
            "— likely selection bias in data, stickers have no causal effect on power output",
            ["spurious-correlation", "no-causation"],
        ),
        (
            "Drivers who run bucket seats tend to also run more boost pressure — survey "
            "correlation with no causal mechanism, seat type does not affect boost",
            ["spurious-correlation", "no-causation"],
        ),
        (
            "Lucky tune day weather correlates with higher dyno results in logged data "
            "— spurious seasonal correlation, not a causal mechanism for power gains",
            ["spurious-correlation", "no-causation"],
        ),
    ]

    for body, extra_tags in causal_entries:
        save_to_shannon(body=body, agent=tr_agent, tags=extra_tags)

    for body, extra_tags in spurious_entries:
        save_to_shannon(body=body, agent=tr_agent, tags=extra_tags)

    time.sleep(2)

    results = query_shannon(
        "What causes increased engine power output and how does octane affect performance?",
        agent=tr_agent,
    )
    entries_list = results.get("entries", []) if isinstance(results, dict) else (results or [])

    top3 = entries_list[:3]
    spurious_in_top3 = sum(
        1 for e in top3
        if isinstance(e, dict) and any(
            t in e.get("tags", [])
            for t in ["spurious-correlation", "no-causation"]
        )
    )
    top3_trust_weights = [
        round(e.get("trust_weight", 0), 2) if isinstance(e, dict) else 0
        for e in top3
    ]
    passed = spurious_in_top3 == 0 and len(top3) >= 3

    duration = time.time() - t0

    result = BenchmarkResult(
        name="Expert Trust Scoring",
        phase=2,
        capability="expert_trust",
        metric="spurious_entries_in_top3",
        value=float(spurious_in_top3),
        target=0.0,
        passed=passed,
        details={
            "spurious_in_top3": spurious_in_top3,
            "top3_trust_weights": top3_trust_weights,
            "total_returned": len(entries_list),
        },
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {spurious_in_top3} spurious in top-3, trust_weights={top3_trust_weights} — {'✅ PASS' if passed else '❌ FAIL'}")
    return result


def phase2_distillation():
    """Test 2.4: Memory distillation (#18) — 5 similar entries condense via POST /distill.

    Saves 5 entries with high word-overlap (Jaccard > 0.5) and 2 meaningful shared tags.
    Calls POST /distill?agent=X&days=90 and verifies at least 1 rule was created.
    """
    log("Phase 2.4: Memory Distillation — 5 similar entries condensed to rule (#18)")
    t0 = time.time()

    di_agent = f"di-{uuid.uuid4().hex[:6]}"

    # 5 entries with very high word overlap (Jaccard > 0.5) to trigger sub-clustering
    similar_entries = [
        "When boost exceeds 12 PSI always retard ignition timing 2 degrees to prevent knock and detonation damage",
        "When boost exceeds 12 PSI retard ignition timing back 2 degrees to prevent detonation knock and damage",
        "Rule: when boost exceeds 12 PSI always retard ignition timing 2 degrees to prevent knock and engine damage",
        "Always retard ignition timing 2 degrees when boost exceeds 12 PSI to prevent detonation and knock damage",
        "Retard ignition timing 2 degrees when boost level exceeds 12 PSI this prevents knock and detonation damage",
    ]

    # Tags: 2 meaningful shared tags required for union-find clustering
    for body in similar_entries:
        save_to_shannon(
            body=body,
            agent=di_agent,
            tags=["boost-tuning", "timing-rule"],
        )

    time.sleep(2)

    r = requests.post(
        f"{SHANNON_URL}/distill",
        params={"agent": di_agent, "days": 90},
        timeout=30,
    )
    distill_data = r.json()
    rules_created = distill_data.get("rules_created", 0)
    groups_found = distill_data.get("groups_found", 0)
    passed = rules_created >= 1

    duration = time.time() - t0

    result = BenchmarkResult(
        name="Memory Distillation",
        phase=2,
        capability="distillation",
        metric="rules_created",
        value=float(rules_created),
        target=1.0,
        passed=passed,
        details={
            "rules_created": rules_created,
            "groups_found": groups_found,
            "rules": distill_data.get("rules", [])[:2],
        },
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {rules_created} rules created from {groups_found} groups — {'✅ PASS' if passed else '❌ FAIL'}")
    return result


# ═══════════════════════════════════════════════════════════════════
# PHASE 2.5: Spurious Collision Test
# Real proof that trust scoring works end-to-end
# ═══════════════════════════════════════════════════════════════════

def phase25_spurious_collision():
    """Test 2.5: Spurious collision — 0 spurious entries in top-3 for causal queries.

    Loads 10 causal entries (tagged 'causal-knowledge','verified') and 10 spurious
    entries (tagged 'spurious-correlation','no-causation') about the same domain.
    Spurious entries are semantically relevant — they mention the same concepts.
    The trust scoring + 0.3× penalty must suppress all spurious entries below top-3
    for every causal query.
    """
    log("Phase 2.5: Spurious Collision — 0 spurious entries in top-3 for causal queries")
    t0 = time.time()

    sc_agent = f"sc-{uuid.uuid4().hex[:6]}"

    causal_entries = [
        "Boost pressure increases air mass per engine cycle, requiring more fuel to maintain stoichiometry — proven causal relationship in fluid dynamics and combustion chemistry",
        "Higher boost pressure requires richer AFR to prevent detonation because compressed air temperature rises at bottom dead center — causal thermodynamics verified on dyno",
        "Intercooler reduces charge air temperature, lowering effective octane requirement and enabling more boost — causal relationship verified by intake air temperature sensors",
        "Lower charge temperature reduces probability of autoignition, enabling more ignition advance without knock — causal mechanism confirmed by cylinder pressure measurement",
        "Timing advance increases peak cylinder pressure and burn rate, directly increasing torque output — causal mechanical relationship proven through pressure transducer data",
        "More ignition advance at low boost is safe because lower combustion temperatures prevent knock — causal thermodynamic relationship confirmed through heat release analysis",
        "Larger turbo takes longer to spool due to higher rotating inertia and turbine housing volume — causal mechanical relationship confirmed on multiple dyno sessions",
        "Higher compression ratio increases thermal efficiency but also increases detonation risk — proven causal relationship between geometry and combustion temperature",
        "E85 allows more timing advance because its heat of vaporization cools the intake charge significantly — causal cooling mechanism measured with intake temperature sensors",
        "Fueling above stoichiometry at WOT provides evaporative cooling, reducing detonation risk under load — causal thermal effect confirmed by EGT and knock sensor data",
    ]

    spurious_entries = [
        "Vehicles with red paint correlate with higher boost pressures in online build threads — likely selection bias in forum data, not a causal effect of color on boost",
        "Turbo cars with carbon fiber hoods tend to have better AFR readings in data logs — spurious correlation due to selection bias toward performance-focused builds",
        "Boost targets correlate with driver age in survey data from car forums — spurious demographic correlation with no causal mechanism between age and boost pressure",
        "Timing values cluster around 18 degrees in many forum builds — statistical artifact from shared tune templates, not a causal recommendation based on engine physics",
        "Engine builds with blue silicone hoses correlate with fewer detonation incidents — selection bias in community data, hose color does not affect detonation risk",
        "AFR readings appear better on cloudy days in dyno session logs — spurious weather correlation, atmospheric humidity confounds the measurement without causal link",
        "Intercooler spray systems correlate with higher power numbers in drag racing databases — selection bias toward serious builds, not a causal effect of spray on charge temp",
        "Premium fuel usage correlates with better horsepower in user surveys — reverse causality, performance-oriented drivers choose premium fuel, not premium causing power",
        "Ignition timing correlates with ambient temperature in logged data across seasons — confounded by seasonal tuning patterns, not a direct causal temperature-timing link",
        "Boost pressure correlates with vehicle age in fleet data — spurious correlation through modification frequency patterns, older vehicles have more accumulated upgrades",
    ]

    for body in causal_entries:
        save_to_shannon(body=body, agent=sc_agent, tags=["causal-knowledge", "verified"])

    for body in spurious_entries:
        save_to_shannon(body=body, agent=sc_agent, tags=["spurious-correlation", "no-causation"])

    time.sleep(3)

    causal_queries = [
        "What is the causal mechanism between boost pressure and air-fuel ratio requirements for turbocharged engines?",
        "How does intercooler efficiency causally affect charge temperature and detonation risk under boost?",
        "What is the proven causal effect of ignition timing advance on combustion and engine power output?",
    ]

    total_top3_checks = 0
    spurious_contaminations = 0
    query_details = []

    for query in causal_queries:
        results = query_shannon(query, agent=sc_agent)
        entries_list = results.get("entries", []) if isinstance(results, dict) else (results or [])

        top3 = entries_list[:3]
        spurious_in_top3 = [
            e.get("body", "")[:60] for e in top3
            if isinstance(e, dict) and any(
                t in e.get("tags", [])
                for t in ["spurious-correlation", "no-causation"]
            )
        ]
        total_top3_checks += 3
        spurious_contaminations += len(spurious_in_top3)
        query_details.append({
            "query": query[:60],
            "spurious_in_top3": len(spurious_in_top3),
            "top3_trust": [round(e.get("trust_weight", 0), 2) if isinstance(e, dict) else 0 for e in top3],
        })

    passed = spurious_contaminations == 0

    duration = time.time() - t0

    result = BenchmarkResult(
        name="Spurious Collision",
        phase=2,
        capability="spurious_collision",
        metric="spurious_entries_in_top3_across_queries",
        value=float(spurious_contaminations),
        target=0.0,
        passed=passed,
        details={
            "spurious_contaminations": spurious_contaminations,
            "total_slots_checked": total_top3_checks,
            "per_query": query_details,
        },
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {spurious_contaminations} spurious entries in top-3 across {len(causal_queries)} queries — {'✅ PASS' if passed else '❌ FAIL'}")
    return result


# ═══════════════════════════════════════════════════════════════════
# PHASE 3: Tenant Isolation
# ═══════════════════════════════════════════════════════════════════

def phase3_agent_isolation():
    """Test 3: Agent isolation — entries from agent A must not appear in agent B queries."""
    log("Phase 3: Agent Isolation (cross-agent bleed test)")
    t0 = time.time()

    agent_a = f"iso-agent-a-{uuid.uuid4().hex[:6]}"
    agent_b = f"iso-agent-b-{uuid.uuid4().hex[:6]}"

    secret_a = f"AGENT_A_SECRET_{uuid.uuid4().hex}"
    secret_b = f"AGENT_B_SECRET_{uuid.uuid4().hex}"

    save_to_shannon(body=f"Secret data: {secret_a}", agent=agent_a, tags=["isolation"])
    save_to_shannon(body=f"Secret data: {secret_b}", agent=agent_b, tags=["isolation"])

    time.sleep(2)

    results_a = query_shannon("Secret data isolation", agent=agent_a)
    results_b = query_shannon("Secret data isolation", agent=agent_b)

    entries_a = results_a.get("entries", []) if isinstance(results_a, dict) else (results_a or [])
    entries_b = results_b.get("entries", []) if isinstance(results_b, dict) else (results_b or [])

    a_sees_b = any(secret_b in (r.get("body", "") if isinstance(r, dict) else str(r)) for r in entries_a)
    b_sees_a = any(secret_a in (r.get("body", "") if isinstance(r, dict) else str(r)) for r in entries_b)

    no_bleed = not a_sees_b and not b_sees_a
    duration = time.time() - t0

    result = BenchmarkResult(
        name="Agent Isolation (zero bleed)",
        phase=3,
        capability="agent_isolation",
        metric="no_cross_agent_bleed",
        value=1.0 if no_bleed else 0.0,
        target=1.0,
        passed=no_bleed,
        details={"a_sees_b": a_sees_b, "b_sees_a": b_sees_a},
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {'✅ ZERO BLEED' if no_bleed else '❌ BLEED DETECTED!'}")
    return result


def phase3_concurrent_load():
    """Test 3b: Concurrent load — 20 simultaneous queries complete without errors."""
    log("Phase 3b: Concurrent Load Test (20 simultaneous queries)")
    t0 = time.time()

    load_agent = f"load-{uuid.uuid4().hex[:6]}"
    queries = [f"concurrent test query number {i} about various topics" for i in range(20)]
    errors = 0
    latencies = []

    def run_query(q):
        qt0 = time.time()
        try:
            r = requests.get(
                f"{SHANNON_URL}/memory",
                params={"agent": load_agent, "topic": q, "limit_tokens": 2000},
                timeout=30,
            )
            r.raise_for_status()
            return time.time() - qt0, None
        except Exception as e:
            return time.time() - qt0, str(e)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(run_query, q): q for q in queries}
        for f in as_completed(futures):
            latency, error = f.result()
            latencies.append(latency)
            if error:
                errors += 1

    p50 = statistics.median(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)]
    success_rate = (len(queries) - errors) / len(queries)
    duration = time.time() - t0

    result = BenchmarkResult(
        name="Concurrent Load (20 queries)",
        phase=3,
        capability="concurrent_load",
        metric="success_rate",
        value=round(success_rate, 3),
        target=1.0,
        passed=success_rate >= 1.0 and p95 < 30.0,  # 30s target on Jetson (single uvicorn + Ollama)
        details={"errors": errors, "p50_s": round(p50, 3), "p95_s": round(p95, 3), "total": len(queries)},
        duration_s=round(duration, 1),
    )
    ALL_RESULTS.append(result)
    log(f"  Result: {success_rate:.1%} success, p50={p50:.3f}s, p95={p95:.3f}s — {'✅ PASS' if result.passed else '❌ FAIL'}")
    return result


# ═══════════════════════════════════════════════════════════════════
# Report
# ═══════════════════════════════════════════════════════════════════

def print_report():
    log("\n" + "=" * 70)
    log("SHANNON V2 STRESS TEST — FULL REPORT")
    log("=" * 70)

    phases = {
        1: "Foundation",
        2: "Feature Validation (#16 trust, #17 conflict, #18 distillation, #19 multi-pass)",
        3: "Isolation & Load",
    }

    for phase_num in sorted(phases.keys()):
        phase_results = [r for r in ALL_RESULTS if r.phase == phase_num]
        if not phase_results:
            continue
        log(f"\n{'─' * 50}")
        log(f"PHASE {phase_num}: {phases[phase_num]}")
        log(f"{'─' * 50}")

        for r in phase_results:
            status = "✅" if r.passed else "❌"
            log(f"  {status} {r.name}")
            log(f"     {r.metric}: {r.value} (target: {r.target})")
            if r.details:
                log(f"     details: {json.dumps(r.details)}")
            log(f"     duration: {r.duration_s}s")

    passed = sum(1 for r in ALL_RESULTS if r.passed)
    total = len(ALL_RESULTS)
    log(f"\n{'=' * 70}")
    log(f"TOTAL: {passed}/{total} passed ({passed / max(total, 1):.0%})")
    log(f"{'=' * 70}")

    results_file = RESULTS_DIR / f"benchmark-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open(results_file, "w") as f:
        json.dump([asdict(r) for r in ALL_RESULTS], f, indent=2)
    log(f"\nResults saved to: {results_file}")


def main():
    log("Starting Shannon V2 Stress Test Suite")
    log(f"Shannon URL: {SHANNON_URL}")
    log(f"Session: {TEST_SESSION}")
    log("")

    # Phase 1: Foundation
    log("═══ PHASE 1: FOUNDATION ═══")
    phase1_embedding_quality()
    phase1_temporal_decay()
    phase1_cross_domain()
    phase1_long_form()
    phase1_source_attribution()
    phase1_multilingual()

    # Phase 2: Feature validation
    log("\n═══ PHASE 2: FEATURE VALIDATION (#16-#19) ═══")
    phase2_multi_pass_retrieval()
    phase2_conflict_detection()
    phase2_expert_trust()
    phase2_distillation()

    # Phase 2.5: Spurious collision
    log("\n═══ PHASE 2.5: SPURIOUS COLLISION ═══")
    phase25_spurious_collision()

    # Phase 3: Isolation
    log("\n═══ PHASE 3: ISOLATION & LOAD ═══")
    phase3_agent_isolation()
    phase3_concurrent_load()

    print_report()


if __name__ == "__main__":
    main()
