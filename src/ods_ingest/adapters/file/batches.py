"""Batch semantics on a streaming bus.

Kafka has no native notion of "the batch is complete", so this module supplies
the convention:

  * batch identity — <fileName>:<sha256[:12]>, so re-delivering the same bytes
    is detectable and the same name with new content is a new batch.
  * control totals — the trailer's declared count and sums are verified against
    what was actually parsed BEFORE anything is produced. A file that does not
    reconcile is quarantined whole; a single unparseable record is dead-lettered
    while the rest of the batch proceeds.
  * a manifest event — emitted after the last detail record is confirmed
    delivered, telling consumers the cycle is closed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ods_ingest.curation.decode import DecodeError, zoned_to_decimal
from ods_ingest.adapters.file import fixed_width as fw

STATUS_COMPLETE = "COMPLETE"
STATUS_FAILED = "FAILED"


def batch_id_for(path: Path) -> str:
    """Identity of one physical delivery: name plus a hash of the content.

    Content-addressed on purpose — a redelivered file is the same batch, while
    a corrected file reusing yesterday's name is not.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"{path.name}:{digest.hexdigest()[:12]}"


@dataclass
class ParsedBatch:
    """The result of reading a custody extract, before anything is produced."""
    batch_id: str
    file_name: str
    cycle_date: str
    header: dict[str, str]
    details: list[dict[str, str]] = field(default_factory=list)
    trailer: Optional[dict[str, str]] = None
    rejects: list[dict[str, str]] = field(default_factory=list)
    control_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.control_error is None

    @property
    def record_count(self) -> int:
        return len(self.details)


def parse_custody_file(path: Path, batch_id: str) -> ParsedBatch:
    """Read a fixed-width custody extract into memory-light parsed records.

    Malformed detail lines are collected as rejects rather than raised: one bad
    record must not cost the whole cycle. Structural problems (no header, no
    trailer) are control errors that fail the batch.
    """
    header: Optional[dict[str, str]] = None
    details: list[dict[str, str]] = []
    trailer: Optional[dict[str, str]] = None
    rejects: list[dict[str, str]] = []

    with open(path, "r", encoding="ascii", errors="replace") as f:
        for rec_type, line_no, line in fw.iter_records(f):
            try:
                if rec_type == fw.REC_TYPE_HEADER:
                    header = fw.parse_header(line)
                elif rec_type == fw.REC_TYPE_DETAIL:
                    details.append(fw.parse_detail(line))
                elif rec_type == fw.REC_TYPE_TRAILER:
                    trailer = fw.parse_trailer(line)
                else:
                    rejects.append({"lineNo": str(line_no), "error":
                                    f"unknown record type {rec_type!r}", "raw": line[:120]})
            except fw.ParseError as exc:
                rejects.append({"lineNo": str(line_no), "error": str(exc), "raw": line[:120]})

    batch = ParsedBatch(
        batch_id=batch_id,
        file_name=path.name,
        cycle_date=(header or {}).get("HDR_BUS_DATE", ""),
        header=header or {},
        details=details,
        trailer=trailer,
        rejects=rejects,
    )
    batch.control_error = _control_error(batch)
    return batch


def sum_control_totals(details: list[dict[str, str]]) -> tuple[Decimal, Decimal]:
    """Sum the two control fields exactly (Decimal, never float)."""
    qty = Decimal(0)
    value = Decimal(0)
    for rec in details:
        qty += zoned_to_decimal(rec["POS_SHR_QTY"], 4)
        value += zoned_to_decimal(rec["POS_MKT_VALUE"], 2)
    return qty, value


def _control_error(batch: ParsedBatch) -> Optional[str]:
    """Verify the file against its own trailer. None means it reconciles."""
    if not batch.header:
        return "missing header record (01)"
    if batch.trailer is None:
        return "missing trailer record (99) — file may be truncated"

    trailer = batch.trailer
    if trailer["TRL_BUS_DATE"] != batch.cycle_date:
        return (f"trailer cycle {trailer['TRL_BUS_DATE']} does not match "
                f"header cycle {batch.cycle_date}")

    try:
        declared_count = int(trailer["TRL_REC_COUNT"])
    except ValueError:
        return f"non-numeric trailer record count {trailer['TRL_REC_COUNT']!r}"

    # Rejected lines still counted toward the mainframe's total, so compare
    # against everything we read, not just what parsed cleanly.
    read_count = len(batch.details) + len(batch.rejects)
    if declared_count != read_count:
        return f"trailer declares {declared_count} records, file contains {read_count}"

    if batch.rejects:
        # Totals cannot reconcile when records failed to parse; the rejects are
        # reported separately and the batch is failed rather than half-trusted.
        return f"{len(batch.rejects)} unparseable detail record(s)"

    try:
        qty, value = sum_control_totals(batch.details)
        declared_qty = zoned_to_decimal(trailer["TRL_TOT_SHR_QTY"], fw.TRAILER_QTY_SCALE)
        declared_value = zoned_to_decimal(trailer["TRL_TOT_MKT_VALUE"], fw.TRAILER_VALUE_SCALE)
    except DecodeError as exc:
        return f"control totals not decodable: {exc}"

    if qty != declared_qty:
        return f"share quantity total {qty} != trailer {declared_qty}"
    if value != declared_value:
        return f"market value total {value} != trailer {declared_value}"
    return None


def build_manifest(batch: ParsedBatch, *, produced: int, extracted_at: str) -> dict:
    """The batch-completeness event published to ods.raw.custody.batches."""
    try:
        qty, value = sum_control_totals(batch.details)
    except DecodeError:
        qty, value = Decimal(0), Decimal(0)

    trailer_count = 0
    if batch.trailer:
        try:
            trailer_count = int(batch.trailer["TRL_REC_COUNT"])
        except ValueError:
            trailer_count = 0

    return {
        "batchId": batch.batch_id,
        "cycleDate": batch.cycle_date,
        "fileName": batch.file_name,
        "recordCount": produced,
        "trailerRecordCount": trailer_count,
        "controlTotalQty": str(qty),
        "controlTotalValue": str(value),
        "status": STATUS_COMPLETE if batch.ok else STATUS_FAILED,
        "failReason": batch.control_error,
        "rejectedCount": len(batch.rejects),
        "extractedAt": extracted_at,
    }


def rec_id_for(cycle_date: str, seq: int) -> str:
    """Loader-assigned REC_ID: the record's position within its batch cycle.

    Matches the convention already documented on RawCustodyPosition, so records
    landed by the pipeline and by the seed script are keyed identically.
    """
    return f"{cycle_date}-{seq:06d}"
