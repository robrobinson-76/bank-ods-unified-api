"""The committed benchmark: bus path vs. direct bulk load, same file.

Answers the question the architecture review put at the centre of the exercise
— what does routing a large EOD file through Kafka actually cost, compared with
parsing it and bulk-writing straight into Mongo?

    python scripts/benchmark_file_ingest.py --records 1000000
    python scripts/benchmark_file_ingest.py --records 50000 --runs 1   # smoke

Both paths parse the SAME file with the SAME parser and write into the SAME
collection with the SAME indexes. The only difference is what sits in between.

Path A (bus)  : file adapter -> Kafka (Avro) -> generic sink -> raw tier
Path B (bulk) : parse -> pymongo insert_many, nothing else

Fairness measures taken:
  * the file is generated once and reused by both paths
  * the target cycle is deleted before each run, so both write into an
    identically-indexed collection with the same starting state
  * the custody topic is recreated before each Path A run, so the sink reads
    exactly the benchmark records and nothing else
  * each path runs `--runs` times and the median is reported
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pymongo
from confluent_kafka.admin import AdminClient, NewTopic

# Running this as a plain script puts scripts/ on sys.path rather than the repo
# root, so the sibling-script imports below would fail. Both `python
# scripts/benchmark_file_ingest.py` and `python -m scripts.benchmark_file_ingest`
# should work.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bank_ods.models.raw_custody_position import RawCustodyPosition  # noqa: E402
from ods_ingest import config  # noqa: E402
from ods_ingest.adapters.file.watcher import CustodyFileAdapter  # noqa: E402
from ods_ingest.bus.consumer import BatchConsumer  # noqa: E402
from ods_ingest.sink import writer  # noqa: E402

import scripts.bulk_load_custody as bulk  # noqa: E402
import scripts.generate_custody_file as generator  # noqa: E402

CUSTODY_TOPIC = "ods.raw.custody.positions"
BENCH_CYCLE = "20990601"


# ── helpers ──────────────────────────────────────────────────────────────────

def _collection() -> pymongo.collection.Collection:
    client: pymongo.MongoClient = pymongo.MongoClient(config.MONGODB_URI)
    return client[config.MONGODB_DB][RawCustodyPosition.COLLECTION]


def _clear_cycle() -> None:
    """Remove the benchmark cycle only — seeded data and indexes stay put."""
    _collection().delete_many({"POS_BUS_DATE": BENCH_CYCLE})


def _count_cycle() -> int:
    return _collection().count_documents({"POS_BUS_DATE": BENCH_CYCLE})


def _recreate_topic(partitions: int = 6) -> None:
    """Give Path A a topic holding only the benchmark records."""
    admin = AdminClient({"bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS})
    for future in admin.delete_topics([CUSTODY_TOPIC]).values():
        try:
            future.result()
        except Exception:  # noqa: BLE001 — absent is fine
            pass
    time.sleep(5)
    new = NewTopic(CUSTODY_TOPIC, num_partitions=partitions, replication_factor=1,
                   config={"retention.ms": str(config.TOPIC_RETENTION_MS)})
    for future in admin.create_topics([new]).values():
        try:
            future.result()
        except Exception as exc:  # noqa: BLE001
            if "already exists" not in str(exc):
                raise
    time.sleep(2)


class FirstDocWatcher:
    """Samples the collection to find when the first record becomes queryable.

    'Landed' and 'queryable' are not the same instant for a streaming path, and
    the difference is one of the things the bus is supposed to buy.
    """

    def __init__(self, poll_interval: float = 0.25):
        self.poll_interval = poll_interval
        self._started_at = 0.0
        self._first_seen: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        collection = _collection()
        while not self._stop.is_set():
            if collection.count_documents({"POS_BUS_DATE": BENCH_CYCLE}, limit=1):
                self._first_seen = time.perf_counter()
                return
            self._stop.wait(self.poll_interval)

    def __enter__(self) -> "FirstDocWatcher":
        self._started_at = time.perf_counter()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def seconds(self) -> Optional[float]:
        if self._first_seen is None:
            return None
        return round(self._first_seen - self._started_at, 3)


def _docker_stats(container: str) -> Optional[dict]:
    """One-shot CPU/memory sample, for the resource note in the findings."""
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.CPUPerc}}|{{.MemUsage}}", container],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return None
        cpu, mem = out.stdout.strip().split("|")
        return {"cpu": cpu, "memory": mem}
    except Exception:  # noqa: BLE001 — a missing sample must not fail the run
        return None


# ── the two paths ────────────────────────────────────────────────────────────

def run_bus_path(landing_dir: Path, source_file: Path, expected: int) -> dict:
    """Path A: adapter -> Kafka -> sink -> raw tier."""
    _clear_cycle()
    _recreate_topic()

    # The adapter consumes the file from the landing directory, so give it a copy.
    target = landing_dir / source_file.name
    if target.resolve() != source_file.resolve():
        target.write_bytes(source_file.read_bytes())

    peak_broker = None
    with FirstDocWatcher() as watcher:
        produce_started = time.monotonic()
        manifests = CustodyFileAdapter().run_once()
        produce_elapsed = time.monotonic() - produce_started
        assert manifests, "the adapter produced nothing — was the file already processed?"
        assert manifests[0]["status"] == "COMPLETE", manifests[0].get("failReason")

        peak_broker = _docker_stats("ods-kafka")

        consume_started = time.monotonic()
        consumer = BatchConsumer(
            [CUSTODY_TOPIC], group_id=f"bench-{int(time.time())}",
            handler=writer.handle, stage="sink",
        )
        try:
            consumer.run_until_idle(idle_timeout=10)
        finally:
            consumer.close()
        # Measure to the last batch actually written, not to the point the
        # consumer gave up waiting for more: run_until_idle always burns its
        # idle timeout before returning, and counting that as work would
        # overstate the bus path by a fixed constant.
        consume_elapsed = (
            (consumer.last_batch_at - consume_started) if consumer.last_batch_at
            else (time.monotonic() - consume_started)
        )

        total = produce_elapsed + consume_elapsed

    landed = _count_cycle()
    return {
        "path": "bus",
        "recordsLanded": landed,
        "expected": expected,
        "complete": landed == expected,
        "elapsedSeconds": round(total, 3),
        "produceSeconds": round(produce_elapsed, 3),
        "consumeSeconds": round(consume_elapsed, 3),
        "recordsPerSecond": round(landed / total, 1) if total else 0,
        "timeToFirstQueryableSeconds": watcher.seconds,
        "brokerStatsDuringRun": peak_broker,
    }


def run_bulk_path(source_file: Path, expected: int, batch_size: int) -> dict:
    """Path B: parse -> insert_many, no bus."""
    _clear_cycle()
    with FirstDocWatcher() as watcher:
        result = bulk.load(source_file, batch_size=batch_size, quiet=True)
    landed = _count_cycle()
    result.update({
        "recordsLanded": landed,
        "expected": expected,
        "complete": landed == expected,
        "timeToFirstQueryableSeconds": watcher.seconds or
        result.get("timeToFirstQueryableSeconds"),
    })
    return result


# ── orchestration ────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=1_000_000)
    parser.add_argument("--runs", type=int, default=3, help="repetitions per path")
    parser.add_argument("--batch-size", type=int, default=bulk.DEFAULT_BATCH_SIZE)
    parser.add_argument("--out", default="benchmark_results.json")
    parser.add_argument("--keep-data", action="store_true",
                        help="leave the benchmark cycle in the collection")
    args = parser.parse_args(argv)

    landing = Path(config.INGEST_LANDING_DIR)
    landing.mkdir(parents=True, exist_ok=True)
    bench_dir = landing.parent / "benchmark"
    bench_dir.mkdir(parents=True, exist_ok=True)

    print(f"generating {args.records:,}-record extract (cycle {BENCH_CYCLE})...")
    gen_started = time.perf_counter()
    assert generator.main([
        "--records", str(args.records), "--cycle-date", BENCH_CYCLE,
        "--out-dir", str(bench_dir), "--seed", "42",
    ]) == 0
    gen_elapsed = time.perf_counter() - gen_started
    source_file = bench_dir / f"CUSTPOS_{BENCH_CYCLE}.dat"
    size_mb = source_file.stat().st_size / (1024 * 1024)
    print(f"  {size_mb:.1f} MB in {gen_elapsed:.1f}s (generation is excluded from both paths)\n")

    runs: list[dict] = []
    for i in range(1, args.runs + 1):
        print(f"run {i}/{args.runs}: bulk path...")
        bulk_result = run_bulk_path(source_file, args.records, args.batch_size)
        print(f"  {bulk_result['elapsedSeconds']}s "
              f"({bulk_result['recordsPerSecond']:,.0f} rec/s)")

        print(f"run {i}/{args.runs}: bus path...")
        # The adapter refuses an already-processed batch, so clear the ledger.
        from ods_ingest import state
        state.clear(f"batch:CUSTPOS_{BENCH_CYCLE}")
        bus_result = run_bus_path(landing, source_file, args.records)
        print(f"  {bus_result['elapsedSeconds']}s "
              f"({bus_result['recordsPerSecond']:,.0f} rec/s; "
              f"produce {bus_result['produceSeconds']}s, "
              f"consume {bus_result['consumeSeconds']}s)")

        runs.append({"run": i, "bulk": bulk_result, "bus": bus_result})

    def median(path: str, key: str) -> float:
        values = [r[path][key] for r in runs if r[path].get(key) is not None]
        return round(statistics.median(values), 3) if values else 0.0

    summary = {
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "records": args.records,
        "fileSizeMB": round(size_mb, 1),
        "runs": args.runs,
        "generationSeconds": round(gen_elapsed, 1),
        "median": {
            "bulkSeconds": median("bulk", "elapsedSeconds"),
            "busSeconds": median("bus", "elapsedSeconds"),
            "bulkRecordsPerSecond": median("bulk", "recordsPerSecond"),
            "busRecordsPerSecond": median("bus", "recordsPerSecond"),
            "busProduceSeconds": median("bus", "produceSeconds"),
            "busConsumeSeconds": median("bus", "consumeSeconds"),
            "bulkTimeToFirstQueryable": median("bulk", "timeToFirstQueryableSeconds"),
            "busTimeToFirstQueryable": median("bus", "timeToFirstQueryableSeconds"),
        },
        "detail": runs,
    }
    summary["median"]["busOverheadFactor"] = (
        round(summary["median"]["busSeconds"] / summary["median"]["bulkSeconds"], 2)
        if summary["median"]["bulkSeconds"] else None
    )

    Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    m = summary["median"]
    print("\n" + "=" * 66)
    print(f"{args.records:,} records ({size_mb:.1f} MB), median of {args.runs} run(s)")
    print("=" * 66)
    print(f"{'':22} {'bulk':>16} {'bus':>16}")
    print(f"{'wall clock (s)':22} {m['bulkSeconds']:>16} {m['busSeconds']:>16}")
    print(f"{'records/sec':22} {m['bulkRecordsPerSecond']:>16,.0f} "
          f"{m['busRecordsPerSecond']:>16,.0f}")
    print(f"{'first queryable (s)':22} {m['bulkTimeToFirstQueryable']:>16} "
          f"{m['busTimeToFirstQueryable']:>16}")
    print(f"\nbus overhead: {m['busOverheadFactor']}x  "
          f"(produce {m['busProduceSeconds']}s + consume {m['busConsumeSeconds']}s)")
    print(f"\nresults written to {args.out}")

    if not args.keep_data:
        _clear_cycle()
        # Also drop the records from the topic. Leaving a million benchmark
        # records there is not harmless: every later consumer group that starts
        # from the beginning — which every ingest test does — would replay them
        # first, turning a 90-second test suite into a 13-minute one.
        _recreate_topic()
        from ods_ingest import state
        state.clear(f"batch:CUSTPOS_{BENCH_CYCLE}")
        print(f"benchmark cycle {BENCH_CYCLE} removed from the collection and the topic")
    return 0


if __name__ == "__main__":
    sys.exit(main())
