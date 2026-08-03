"""Parser for the intraday cash-movement drops.

Delimited rather than fixed-width, and small rather than enormous — the second
format proves the file adapter's batch machinery (identity, idempotency,
archiving) is genuinely feed-agnostic, with only the parser swapped.

As with the custody extract, values are captured verbatim. MOV_AMT keeps its
plain signed-decimal spelling; MOV_VALUE_TS keeps the source's own timestamp
format. Interpretation happens in curation.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterator

CASH_TOPIC = "ods.raw.cash.movements"
CASH_PATTERN = "CASHMOV_*.csv"

# CASHMOV_<CCYYMMDD>_<HHMM>.csv — the drop's business date and time of day.
FILE_NAME_RE = re.compile(r"CASHMOV_(?P<date>\d{8})_(?P<time>\d{4})\.csv$", re.IGNORECASE)

COLUMNS = [
    "MOV_ACCT_NBR", "MOV_CCY_CD", "MOV_AMT", "MOV_TYPE_CD",
    "MOV_VALUE_TS", "MOV_NARRATIVE", "MOV_SRC_SYS_ID",
]


class CashParseError(ValueError):
    """The drop is not a cash-movement file this adapter can read."""


def file_metadata(path: Path) -> tuple[str, str]:
    """(CCYYMMDD, HHMM) from the file name — the source states them nowhere else."""
    match = FILE_NAME_RE.search(path.name)
    if not match:
        raise CashParseError(
            f"{path.name} does not match {CASH_PATTERN} (expected CASHMOV_<date>_<time>.csv)"
        )
    return match.group("date"), match.group("time")


def parse_cash_file(path: Path, batch_id: str) -> Iterator[dict]:
    """Yield one raw cash-movement record per data row."""
    file_date, file_time = file_metadata(path)

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise CashParseError(f"{path.name} is missing column(s): {missing}")

        for seq, row in enumerate(reader, 1):
            yield {
                # Loader-assigned, mirroring the custody convention: the row's
                # position within its drop makes re-delivery idempotent.
                "MOVEMENT_ID": f"{batch_id}-{seq:05d}",
                "MOV_FILE_DATE": file_date,
                "MOV_FILE_TIME": file_time,
                **{column: (row.get(column) or "").strip() for column in COLUMNS},
            }
