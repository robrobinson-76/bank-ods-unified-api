"""How fast can the bus path go, and how does it compare to a direct bulk load?

The file-ingest benchmark measured ONE bus configuration — a single sink, stock
producer settings — and found it ~6× slower than `pymongo` bulk writes. That
left the obvious question unanswered: the 6× is against an untuned, unscaled
bus, so how much of the gap is inherent and how much is configuration?

This measures the levers, in the order anyone would actually try them:

  produce leg   compression codec, linger/batch size, acks + idempotence
  consume leg   parallel sink instances across the topic's partitions,
                consumer batch size (which drives the Mongo bulk write size)
  reference     pymongo bulk write, straight into the same collection

    python scripts/benchmark_bus_tuning.py --records 1000000

Parallel consumers are run as separate PROCESSES, not threads — that is how
they would be deployed (one pod per instance), and it is the only way to get
real parallelism out of a CPU-bound decode-and-validate path in Python.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import pymongo
from confluent_kafka.admin import AdminClient, NewTopic

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bank_ods.models.raw_custody_position import RawCustodyPosition  # noqa: E402
from ods_ingest import config  # noqa: E402
from ods_ingest.adapters.file.watcher import CustodyFileAdapter  # noqa: E402

import scripts.bulk_load_custody as bulk  # noqa: E402
import scripts.generate_custody_file as generator  # noqa: E402

TOPIC = "ods.raw.custody.positions"
BENCH_CYCLE = "20990701"
PARTITIONS = 6


def _collection() -> pymongo.collection.Collection:
    client: pymongo.MongoClient = pymongo.MongoClient(config.MONGODB_URI)
    return client[config.MONGODB_DB][RawCustodyPosition.COLLECTION]


def _clear() -> None:
    _collection().delete_many({"POS_BUS_DATE": BENCH_CYCLE})


def _count() -> int:
    return _collection().count_documents({"POS_BUS_DATE": BENCH_CYCLE})


def _recreate_topic() -> None:
    admin = AdminClient({"bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS})
    for future in admin.delete_topics([TOPIC]).values():
        try:
            future.result()
        except Exception:  # noqa: BLE001 — absent is fine
            pass
    time.sleep(5)
    new = NewTopic(TOPIC, num_partitions=PARTITIONS, replication_factor=1,
                   config={"retention.ms": str(config.TOPIC_RETENTION_MS)})
    for future in admin.create_topics([new]).values():
        try:
            future.result()
        except Exception as exc:  # noqa: BLE001
            if "already exists" not in str(exc):
                raise
    time.sleep(2)


# ── produce leg ──────────────────────────────────────────────────────────────

PRODUCER_VARIANTS: list[tuple[str, dict[str, Any]]] = [
    ("stock (lz4, linger 20ms, acks=all, idempotent)", {}),
    ("no compression", {"compression.type": "none"}),
    ("zstd", {"compression.type": "zstd"}),
    ("snappy", {"compression.type": "snappy"}),
    ("bigger batches (linger 100ms, 4MB)", {"linger.ms": 100, "batch.size": 4 * 1024 * 1024}),
    ("acks=1, no idempotence (UNSAFE — reference only)",
     {"acks": "1", "enable.idempotence": False}),
]


def measure_produce(landing: Path, source: Path, label: str,
                    overrides: dict[str, Any]) -> dict:
    """Time the adapter's parse-and-produce leg under one producer config."""
    from ods_ingest import state

    _recreate_topic()
    state.clear(f"batch:CUSTPOS_{BENCH_CYCLE}")
    target = landing / source.name
    if target.resolve() != source.resolve():
        target.write_bytes(source.read_bytes())

    started = time.monotonic()
    manifests = CustodyFileAdapter(producer_overrides=overrides).run_once()
    elapsed = time.monotonic() - started
    assert manifests and manifests[0]["status"] == "COMPLETE", "produce leg failed"

    records = manifests[0]["recordCount"]
    return {"variant": label, "seconds": round(elapsed, 2),
            "recordsPerSecond": round(records / elapsed) if elapsed else 0}


# ── consume leg ──────────────────────────────────────────────────────────────

def measure_consume(instances: int, expected: int, batch_size: int,
                    timeout_s: float = 900) -> dict:
    """Run N sink processes in one consumer group and time the landing.

    Measured by polling the collection until it reaches `expected`, which is
    the only way to time work spread across processes — and is also what an
    operator actually cares about ("when is the data queryable?").
    """
    _clear()
    group = f"bench-{instances}x-{batch_size}-{int(time.time())}"
    env_batch = {"CONSUMER_BATCH_SIZE": str(batch_size)}

    procs: list[subprocess.Popen] = []
    started = time.monotonic()
    import os

    env = {**os.environ, **env_batch}
    for _ in range(instances):
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "ods_ingest.sink", "--once",
             "--group-id", group, "--topics", TOPIC, "--idle-timeout", "10"],
            cwd=_REPO_ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))

    elapsed: Optional[float] = None
    deadline = started + timeout_s
    while time.monotonic() < deadline:
        if _count() >= expected:
            elapsed = time.monotonic() - started
            break
        time.sleep(0.5)

    for p in procs:
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()

    landed = _count()
    if elapsed is None:
        elapsed = time.monotonic() - started
    return {
        "instances": instances, "consumerBatchSize": batch_size,
        "seconds": round(elapsed, 2),
        "recordsPerSecond": round(landed / elapsed) if elapsed else 0,
        "landed": landed, "complete": landed >= expected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=1_000_000)
    parser.add_argument("--instances", type=int, nargs="+", default=[1, 2, 3, 6])
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[500, 5000])
    parser.add_argument("--skip-producer", action="store_true")
    parser.add_argument("--out", default="bus_tuning_results.json")
    args = parser.parse_args(argv)

    landing = Path(config.INGEST_LANDING_DIR)
    landing.mkdir(parents=True, exist_ok=True)
    bench_dir = landing.parent / "benchmark"
    bench_dir.mkdir(parents=True, exist_ok=True)

    print(f"generating {args.records:,}-record extract...")
    assert generator.main([
        "--records", str(args.records), "--cycle-date", BENCH_CYCLE,
        "--out-dir", str(bench_dir), "--seed", "42",
    ]) == 0
    source = bench_dir / f"CUSTPOS_{BENCH_CYCLE}.dat"
    size_mb = source.stat().st_size / (1024 * 1024)
    print(f"  {size_mb:.1f} MB\n")

    results: dict[str, Any] = {"records": args.records, "fileSizeMB": round(size_mb, 1),
                              "partitions": PARTITIONS}

    # ── reference: straight to Mongo ──
    print("reference: pymongo bulk write, no bus")
    _clear()
    ref = bulk.load(source, quiet=True)
    results["bulk"] = {"seconds": ref["elapsedSeconds"],
                       "recordsPerSecond": ref["recordsPerSecond"]}
    print(f"  {ref['elapsedSeconds']}s ({ref['recordsPerSecond']:,.0f} rec/s)\n")

    # ── produce leg variants ──
    if not args.skip_producer:
        print("produce leg (file -> Kafka), by producer configuration")
        produce_runs = []
        for label, overrides in PRODUCER_VARIANTS:
            run = measure_produce(landing, source, label, overrides)
            produce_runs.append(run)
            print(f"  {label:52} {run['seconds']:7.1f}s  "
                  f"{run['recordsPerSecond']:>9,} rec/s")
        results["produce"] = produce_runs
        print()

    # ── consume leg: parallelism and batch size ──
    # Load the topic once with the stock producer, then re-read it with each
    # consumer configuration using a fresh group.
    print("loading the topic once for the consume measurements...")
    measure_produce(landing, source, "setup", {})
    print()

    print("consume leg (Kafka -> validate -> Mongo), by sink instances")
    consume_runs = []
    for batch_size in args.batch_sizes:
        for instances in args.instances:
            run = measure_consume(instances, args.records, batch_size)
            consume_runs.append(run)
            flag = "" if run["complete"] else "  (INCOMPLETE)"
            print(f"  {instances} instance(s), batch {batch_size:>5}         "
                  f"{run['seconds']:7.1f}s  {run['recordsPerSecond']:>9,} rec/s{flag}")
    results["consume"] = consume_runs

    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")

    # ── summary ──
    best_consume = min((r for r in consume_runs if r["complete"]),
                       key=lambda r: r["seconds"], default=None)
    baseline = next((r for r in consume_runs
                     if r["instances"] == 1 and r["consumerBatchSize"] == args.batch_sizes[0]),
                    None)
    print("\n" + "=" * 72)
    print(f"{args.records:,} records ({size_mb:.1f} MB)")
    print("=" * 72)
    print(f"  pymongo bulk write (no bus)      {results['bulk']['seconds']:>8.1f}s")
    if baseline:
        print(f"  bus, 1 sink, batch {args.batch_sizes[0]:<5}         "
              f"{baseline['seconds']:>8.1f}s   consume leg only")
    if best_consume:
        print(f"  bus, best consume config          {best_consume['seconds']:>8.1f}s   "
              f"({best_consume['instances']} instances, batch "
              f"{best_consume['consumerBatchSize']})")
        if baseline and best_consume["seconds"]:
            print(f"  consumer scaling speedup          "
                  f"{baseline['seconds'] / best_consume['seconds']:>8.2f}x")
    print(f"\nresults written to {args.out}")

    _clear()
    _recreate_topic()
    from ods_ingest import state
    state.clear(f"batch:CUSTPOS_{BENCH_CYCLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
