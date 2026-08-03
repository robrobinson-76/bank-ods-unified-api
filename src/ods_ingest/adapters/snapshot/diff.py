"""Sort-merge delta between two full-population snapshots.

The previous snapshot is retained as a sorted key index — key, source
timestamp, content hash — not as the records themselves. At 40M keys that is
roughly 1.5 GB of local disk, and it is all that is needed to answer "what
changed?".

Both sides are consumed as sorted streams and merged in one pass, so the cost
is two sequential scans with no random access anywhere. Three outcomes fall
out of the merge directly:

    key in both, hash differs   -> CHANGED
    key only on the right       -> ADDED
    key only on the left        -> REMOVED   (absence is the delete signal)

A record whose hash is unchanged produces nothing at all, which is the point.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, TypeVar


class Change(str, Enum):
    ADDED = "ADDED"
    CHANGED = "CHANGED"
    REMOVED = "REMOVED"


@dataclass(frozen=True)
class IndexEntry:
    """One line of the retained key index."""
    key: str
    source_timestamp: str
    content_hash: str

    def to_line(self) -> str:
        return f"{self.key}\t{self.source_timestamp}\t{self.content_hash}"

    @classmethod
    def from_line(cls, line: str) -> "IndexEntry":
        key, timestamp, digest = line.rstrip("\n").split("\t")
        return cls(key, timestamp, digest)


@dataclass(frozen=True)
class Delta:
    """One difference to produce to the bus."""
    change: Change
    key: str
    # None for REMOVED — the record is gone from the source, and only the key
    # and the previous state are known.
    record: Optional[dict]
    source_timestamp: str


def content_hash(record: dict, *, exclude: Iterable[str] = ()) -> str:
    """Stable digest of a record's content.

    Excludes fields that vary per delivery rather than per record (a batch id,
    an extraction time); if those were included, every record would look
    changed on every snapshot and the diff would be worthless.
    """
    skip = set(exclude)
    payload = {k: v for k, v in sorted(record.items()) if k not in skip}
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def read_index(path: Path) -> Iterator[IndexEntry]:
    """Stream the retained index. A missing file means 'no previous snapshot'."""
    if not path.exists():
        return iter(())

    def _iter() -> Iterator[IndexEntry]:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield IndexEntry.from_line(line)

    return _iter()


def write_index(path: Path, entries: Iterable[IndexEntry]) -> int:
    """Persist the new index atomically — a half-written index is worse than none.

    A truncated index would make the next run treat the missing tail as
    REMOVED, emitting a mass of spurious deletions.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for entry in entries:
            f.write(entry.to_line() + "\n")
            count += 1
    tmp.replace(path)
    return count


@dataclass
class DiffStats:
    added: int = 0
    changed: int = 0
    removed: int = 0
    unchanged: int = 0

    @property
    def total_seen(self) -> int:
        return self.added + self.changed + self.unchanged

    @property
    def emitted(self) -> int:
        return self.added + self.changed + self.removed

    def as_dict(self) -> dict:
        return {
            "added": self.added, "changed": self.changed, "removed": self.removed,
            "unchanged": self.unchanged, "recordsInSnapshot": self.total_seen,
            "emitted": self.emitted,
            "suppressionRatio": (
                round(1 - self.emitted / self.total_seen, 4) if self.total_seen else 0.0
            ),
        }


T = TypeVar("T")


def _checked(stream: Iterator[T], key_of: Callable[[T], str], label: str) -> Iterator[T]:
    """Pass a stream through, rejecting keys that are not strictly ascending.

    Sortedness and uniqueness are what make the merge linear and correct.
    Accepting an unsorted stream silently would emit spurious REMOVED/ADDED
    pairs — that is, soft-delete live securities and immediately re-add them.
    """
    last: Optional[str] = None
    for item in stream:
        key = key_of(item)
        if last is not None and key <= last:
            raise ValueError(f"{label} is not sorted/unique at {key!r}")
        last = key
        yield item


def sort_merge(
    previous: Iterator[IndexEntry],
    incoming: Iterator[tuple[str, str, dict]],
    stats: Optional[DiffStats] = None,
) -> Iterator[tuple[Optional[Delta], Optional[IndexEntry]]]:
    """Merge two key-sorted streams into (delta, new index entry) pairs.

    `incoming` yields (key, source_timestamp, record) in ascending key order.
    Either element of the yielded pair may be None, and both cases are load
    bearing:

        (delta, entry)   added or changed — produce it, and index it
        (None,  entry)   unchanged — produce nothing, but still index it, since
                         the new index must describe the whole population
        (delta, None)    removed — produce the deletion, and drop it from the
                         index

    Both streams must be sorted by key; that precondition is what makes this
    linear, so it is checked rather than assumed.
    """
    stats = stats if stats is not None else DiffStats()

    # Validate ordering as each stream is consumed. Checking inside the merge
    # loop instead would re-examine whichever side did not advance and reject
    # every legitimate removal. The names are rebound rather than aliased so
    # there is no unchecked stream left in scope to pull from by accident.
    previous = _checked(previous, lambda e: e.key, "previous index")
    incoming = _checked(incoming, lambda r: r[0], "incoming snapshot")

    left = next(previous, None)
    right = next(incoming, None)

    while left is not None or right is not None:
        # Present before, absent now: the source has removed it.
        if left is not None and (right is None or left.key < right[0]):
            stats.removed += 1
            yield Delta(Change.REMOVED, left.key, None, left.source_timestamp), None
            left = next(previous, None)
            continue

        # Past the removal branch, `right` is necessarily present: the loop runs
        # while either side has data, and every case where right is exhausted is
        # handled above.
        assert right is not None
        key, timestamp, record = right
        entry = IndexEntry(key, timestamp, content_hash(record))

        # Absent before, present now.
        if left is None or key < left.key:
            stats.added += 1
            yield Delta(Change.ADDED, key, record, timestamp), entry
            right = next(incoming, None)
            continue

        # Same key both sides: emit only if the content actually moved.
        if left.content_hash != entry.content_hash:
            stats.changed += 1
            yield Delta(Change.CHANGED, key, record, timestamp), entry
        else:
            stats.unchanged += 1
            yield None, entry
        left = next(previous, None)
        right = next(incoming, None)
