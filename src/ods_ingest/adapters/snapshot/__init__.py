"""Snapshot adapter — full-population true-up files.

For sources that deliver their entire population periodically (a vendor
reference master of tens of millions of records) alongside an intraday stream.

The whole file arrives and is verified, but only what actually *differs* is
produced to the bus. That is the difference between a feed that fits its window
and one that does not: a 40M-record file typically carries a few hundred
thousand real changes, and conditional writes at the database do not help —
they still pay a lookup per record.

The diff is a linear sort-merge against the previous snapshot's key index, so
it costs two sequential scans rather than N random lookups, and it detects
removals — which a hash comparison alone cannot, because in a full-population
snapshot **absence is information**.

See docs/PATTERN-snapshot-and-stream.md.
"""
