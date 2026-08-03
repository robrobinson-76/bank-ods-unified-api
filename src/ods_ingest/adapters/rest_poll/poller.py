"""Watermark-driven polling of the vendor SaaS.

The correctness rules, in order of how easy they are to get wrong:

  1. The watermark advances only AFTER every record in the window has been
     confirmed delivered to Kafka. Advancing on receipt would silently skip
     records whenever a produce failed.
  2. Each poll re-requests a small overlap before the watermark, because a
     source can commit a record with a timestamp slightly behind one already
     returned. Duplicates are free — the sink upserts on Vendor_Ref.
  3. Rate limits and 5xx are retried with backoff, and the watermark is left
     untouched on failure, so the next run resumes rather than skips.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from ods_ingest import config, state, topics
from ods_ingest.bus.producer import TopicProducer
from ods_ingest.curation.decode import source_timestamp_iso
from ods_ingest.envelope import build_headers, utc_now_iso

log = logging.getLogger("ods_ingest.rest_poll")

TOPIC = "ods.raw.vendorsec.securities"
SOURCE = "vendorsec"
ADAPTER_ID = "rest-poll-adapter"
ADAPTER_VERSION = "1.0.0"

# The stub's change-tracking column. It drives the watermark but is not part of
# the raw model, so it is stripped before the record goes on the wire.
WATERMARK_FIELD = "updated_at"

MAX_ATTEMPTS = 5
EPOCH = "1970-01-01T00:00:00+00:00"


class PollResult:
    def __init__(self) -> None:
        self.pages = 0
        self.records = 0
        self.retries = 0
        self.watermark: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {"pages": self.pages, "records": self.records, "retries": self.retries,
                "watermark": self.watermark}


def to_raw_record(row: dict) -> dict:
    """Vendor row to the raw-tier record shape.

    Two mechanical adjustments, and nothing else — no interpretation:

    * `updated_at` is dropped. It is the API's delivery metadata (what the
      watermark walks), not part of the record the vendor describes.
    * `SRC_UPDATED_AT` is added: the vendor's own LAST_UPD_TS parsed into a
      sortable ISO instant. This entity is fed by two channels, so it needs a
      comparable ordering value, and LAST_UPD_TS is not comparable as delivered
      (see curation/decode.py:source_timestamp).

    Where LAST_UPD_TS is missing or unparseable, the API's own `updated_at` is
    the fallback — it is a genuine source-side ordering signal, just a coarser
    one. A record with neither is left unstamped, and the sink will then only
    insert it if absent rather than let it overwrite a stamped record.
    """
    record = {k: v for k, v in row.items() if k != WATERMARK_FIELD}
    record["SRC_UPDATED_AT"] = source_timestamp_iso(
        row.get("LAST_UPD_TS"),
        default=source_timestamp_iso(row.get(WATERMARK_FIELD)),
    )
    return record


def _overlap_start(watermark: Optional[str]) -> str:
    """Where to ask from: the watermark minus the overlap window."""
    if not watermark:
        return EPOCH
    try:
        parsed = datetime.fromisoformat(watermark)
    except ValueError:
        return EPOCH
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed - timedelta(seconds=config.POLL_OVERLAP_S)).isoformat()


def _fetch_page(client: httpx.Client, since: str, page: int, result: PollResult) -> dict:
    """One page, with backoff on rate limits and transient failures."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = client.get(
            "/securities",
            params={"updated_since": since, "page": page, "page_size": config.SAAS_PAGE_SIZE},
        )
        if response.status_code == 200:
            return response.json()

        if response.status_code == 429:
            wait = float(response.headers.get("Retry-After", "1"))
            log.warning("rate limited on page %d; waiting %.1fs", page, wait)
        elif response.status_code >= 500:
            wait = min(2 ** attempt * 0.25, 8.0)
            log.warning("server error %d on page %d; retrying in %.1fs",
                        response.status_code, page, wait)
        else:
            response.raise_for_status()
            wait = 0.0

        result.retries += 1
        if attempt == MAX_ATTEMPTS:
            raise RuntimeError(
                f"giving up on page {page} after {MAX_ATTEMPTS} attempts "
                f"(last status {response.status_code})"
            )
        time.sleep(wait)
    raise AssertionError("unreachable")


def poll_once(base_url: Optional[str] = None, *, full_resync: bool = False) -> PollResult:
    """One incremental sweep: read from the watermark, produce, then advance it."""
    result = PollResult()
    if full_resync:
        # Backfill is simply forgetting the position — no special code path.
        state.reset_watermark(SOURCE)

    watermark = state.get_watermark(SOURCE)
    since = _overlap_start(watermark)
    log.info("polling %s since %s (watermark=%s)", SOURCE, since, watermark)

    spec = topics.get(TOPIC)
    highest = watermark
    url = base_url or config.SAAS_BASE_URL

    with httpx.Client(base_url=url, timeout=30.0) as client, \
            TopicProducer(TOPIC, adapter_id=ADAPTER_ID,
                          adapter_version=ADAPTER_VERSION) as producer:
        page = 1
        while True:
            payload = _fetch_page(client, since, page, result)
            rows = payload.get("data") or []
            if not rows:
                break

            extracted_at = utc_now_iso()
            for row in rows:
                producer.produce(to_raw_record(row), headers=build_headers(
                    source_system=spec.source_system,
                    adapter_id=ADAPTER_ID,
                    adapter_version=ADAPTER_VERSION,
                    extracted_at=extracted_at,
                ))
                result.records += 1
                row_watermark = row.get(WATERMARK_FIELD)
                if row_watermark and (highest is None or row_watermark > highest):
                    highest = row_watermark

            result.pages += 1
            if not payload.get("has_more"):
                break
            page += 1
        # Leaving the producer context flushes and raises on any delivery
        # failure — so the watermark below is only reached if everything landed.

    if highest and highest != watermark:
        state.set_watermark(SOURCE, highest, records=result.records)
    result.watermark = highest
    log.info("poll complete: %s", result.as_dict())
    return result


def run_forever(base_url: Optional[str] = None) -> None:
    log.info("polling %s every %.0fs", config.SAAS_BASE_URL, config.POLL_INTERVAL_S)
    try:
        while True:
            try:
                poll_once(base_url)
            except Exception as exc:  # noqa: BLE001 — a failed poll must not kill the loop
                log.error("poll failed, watermark unchanged: %s", exc)
            time.sleep(config.POLL_INTERVAL_S)
    except KeyboardInterrupt:
        log.info("rest poll adapter stopping")
    finally:
        state.close()
