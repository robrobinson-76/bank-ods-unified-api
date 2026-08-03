"""REST polling adapter — the legacy SaaS that only offers an API.

Converts pull-based incremental capture into the same push-style Avro records
every other feed produces. The interesting state is the watermark: the adapter
remembers how far it has read, re-requests a small overlap to tolerate
out-of-order commits, and only advances after deliveries are confirmed.

What polling structurally cannot do is see deletions — see
docs/FINDINGS-rest-polling.md.
"""
