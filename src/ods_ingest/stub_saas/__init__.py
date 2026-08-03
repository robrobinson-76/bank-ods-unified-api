"""Stub vendor SaaS — the legacy source that only offers a REST API.

Stands in for a third-party security-master service. Backed by a fixed JSON
dataset in this package, so it has no database and behaves identically on every
run — the REST adapter's tests are deterministic.

It is deliberately unhelpful in the ways real vendor APIs are: incremental
queries only by a coarse `updated_since` timestamp, page-at-a-time delivery,
rate limiting, occasional 500s, and no way at all to learn about deletions.

    uvicorn ods_ingest.stub_saas:app --port 8010
"""
from ods_ingest.stub_saas.app import app

__all__ = ["app"]
