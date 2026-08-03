"""File adapter — the legacy system that drops flat files.

Handles both cadences with one framework: a large end-of-day fixed-width
custody extract and small intraday delimited cash drops. The batch machinery
(control totals, batch identity, manifests, quarantine) is shared; only the
parser differs per feed.
"""
