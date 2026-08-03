"""Write-pattern microbenchmark for start-of-day true-up loads.

The file-ingest benchmark compared the bus (upsert) against a bulk loader doing
blind `insert_many`. That is the right comparison for an append-style feed, and
the WRONG one for a start-of-day true-up, where both paths must do
"update if newer, insert if absent" against a populated collection.

This isolates the write strategy from the transport so the true-up question can
be reasoned about separately:

  S1  insert_many, empty collection      — the append baseline
  S2  ReplaceOne(upsert), 0% changed     — naive true-up: rewrite everything
  S3  UpdateOne(version guard), 0% changed — update-if-newer with nothing to do
  S4  UpdateOne(version guard), 1% changed — a realistic daily drift rate

S2 vs S3 is the measurement that matters: what a full-population snapshot costs
when almost nothing in it is new.

    python scripts/benchmark_trueup_writes.py --records 1000000

Uses its own collection and drops it afterwards; the ODS collections are
untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterator

import pymongo
from pymongo import InsertOne, ReplaceOne, UpdateOne
from pymongo.errors import BulkWriteError

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from ods_ingest import config  # noqa: E402

COLLECTION = "bench_trueup"
BATCH = 5_000


def make_docs(count: int, *, version: int, changed_every: int = 0) -> Iterator[list[dict]]:
    """Yield batches of reference-data-shaped documents.

    `changed_every` = N means every Nth record carries the new version (i.e.
    genuinely changed); the rest keep version 1. 0 means nothing changed.
    """
    batch: list[dict] = []
    for i in range(count):
        is_changed = changed_every and (i % changed_every == 0)
        batch.append({
            "REC_ID": f"SEC-{i:09d}",
            "SEC_DESC": f"INSTRUMENT {i:09d} ORDINARY SHARES",
            "SEC_CCY": "USD",
            "SEC_CNTRY": "US",
            "SEC_STATUS": "ACTIVE",
            "SEC_PRICE": f"{(i % 100000) / 100:.2f}",
            # The source-side version the "if newer" test is made against.
            "SRC_VERSION": version if is_changed else 1,
        })
        if len(batch) >= BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


def timed(label: str, fn) -> dict:
    started = time.perf_counter()
    written = fn()
    elapsed = time.perf_counter() - started
    result = {
        "strategy": label,
        "elapsedSeconds": round(elapsed, 2),
        "docsAffected": written,
    }
    print(f"  {label:52} {elapsed:8.2f}s")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=1_000_000)
    parser.add_argument("--change-rate", type=float, default=0.01,
                        help="fraction genuinely changed in S4 (default 1%%)")
    parser.add_argument("--out", default="trueup_results.json")
    args = parser.parse_args(argv)

    n = args.records
    changed_every = int(1 / args.change_rate) if args.change_rate > 0 else 0

    client: pymongo.MongoClient = pymongo.MongoClient(config.MONGODB_URI)
    col = client[config.MONGODB_DB][COLLECTION]
    col.drop()
    col.create_index("REC_ID", unique=True)

    print(f"\n{n:,} reference records, {args.change_rate:.1%} change rate\n")
    results = []

    # ── S1: append baseline — blind insert into an empty collection ──────────
    def s1() -> int:
        total = 0
        for batch in make_docs(n, version=1):
            col.bulk_write([InsertOne(d) for d in batch], ordered=False)
            total += len(batch)
        return total

    results.append(timed("S1  insert_many, empty collection (append baseline)", s1))

    # ── S2: naive true-up — replace every document, nothing actually changed ─
    def s2() -> int:
        total = 0
        for batch in make_docs(n, version=2):
            res = col.bulk_write(
                [ReplaceOne({"REC_ID": d["REC_ID"]}, d, upsert=True) for d in batch],
                ordered=False,
            )
            total += res.modified_count + res.upserted_count
        return total

    results.append(timed("S2  ReplaceOne upsert, 0% changed (naive true-up)", s2))

    # ── S3: update-if-newer with nothing to do ──────────────────────────────
    # The version guard means the filter matches nothing, so Mongo does no write.
    # upsert=False deliberately: an upsert here would try to INSERT when the
    # guard fails and collide on the unique key (see the findings note).
    def s3() -> int:
        total = 0
        for batch in make_docs(n, version=2):
            res = col.bulk_write(
                [
                    UpdateOne(
                        {"REC_ID": d["REC_ID"], "SRC_VERSION": {"$lt": d["SRC_VERSION"]}},
                        {"$set": d},
                    )
                    for d in batch
                ],
                ordered=False,
            )
            total += res.modified_count
        return total

    results.append(timed("S3  UpdateOne version guard, 0% changed (if-newer)", s3))

    # ── S4: update-if-newer with a realistic daily change rate ──────────────
    def s4() -> int:
        total = 0
        for batch in make_docs(n, version=3, changed_every=changed_every):
            ops = [
                UpdateOne(
                    {"REC_ID": d["REC_ID"], "SRC_VERSION": {"$lt": d["SRC_VERSION"]}},
                    {"$set": d},
                )
                for d in batch
            ]
            try:
                res = col.bulk_write(ops, ordered=False)
                total += res.modified_count
            except BulkWriteError:
                pass
        return total

    results.append(timed(
        f"S4  UpdateOne version guard, {args.change_rate:.0%} changed (if-newer)", s4))

    stored = col.count_documents({})
    col.drop()
    client.close()

    baseline = results[0]["elapsedSeconds"]
    summary = {
        "records": n,
        "changeRate": args.change_rate,
        "docsInCollection": stored,
        "results": results,
        "relativeToAppendBaseline": {
            r["strategy"][:3].strip(): round(r["elapsedSeconds"] / baseline, 2)
            for r in results
        },
    }
    Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n  {'':52} {'vs S1':>8}")
    for r in results:
        print(f"  {r['strategy']:52} {r['elapsedSeconds'] / baseline:7.2f}x")
    print(f"\nresults written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
