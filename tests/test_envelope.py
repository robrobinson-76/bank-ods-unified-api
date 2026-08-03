"""Canonical ingestion envelope — header codec.

Core suite: pure encode/decode, no infrastructure.
"""
from ods_ingest import envelope as env


def test_build_headers_sets_the_canonical_set():
    headers = env.build_headers(
        source_system="MAINFRAME_CUSTODY",
        adapter_id="file-adapter",
        adapter_version="1.0.0",
        batch_id="CUSTPOS_20260730.dat:abc123def456",
        record_seq=7,
        extracted_at="2026-07-30T12:00:00+00:00",
    )
    decoded = env.decode_headers(headers)
    assert decoded == {
        env.H_SOURCE_SYSTEM: "MAINFRAME_CUSTODY",
        env.H_ADAPTER_ID: "file-adapter",
        env.H_ADAPTER_VERSION: "1.0.0",
        env.H_EXTRACTED_AT: "2026-07-30T12:00:00+00:00",
        env.H_BATCH_ID: "CUSTPOS_20260730.dat:abc123def456",
        env.H_RECORD_SEQ: "7",
    }


def test_batch_headers_omitted_for_streaming_sources():
    """Absence is meaningful: a streaming record has no batch, not an empty one."""
    decoded = env.decode_headers(
        env.build_headers(
            source_system="VENDORSEC_SAAS", adapter_id="rest-poll", adapter_version="1.0.0"
        )
    )
    assert env.H_BATCH_ID not in decoded
    assert env.H_RECORD_SEQ not in decoded
    assert env.record_seq_of(decoded) is None


def test_extracted_at_defaults_to_now_in_utc():
    decoded = env.decode_headers(
        env.build_headers(source_system="S", adapter_id="a", adapter_version="1")
    )
    # ISO 8601 with an explicit UTC offset, matching the ODS serialization rule.
    assert decoded[env.H_EXTRACTED_AT].endswith("+00:00")


def test_record_seq_round_trips_as_int():
    decoded = env.decode_headers(
        env.build_headers(
            source_system="S", adapter_id="a", adapter_version="1", record_seq=0
        )
    )
    # Zero is a real sequence number, not a missing one.
    assert env.record_seq_of(decoded) == 0


def test_decode_tolerates_records_we_did_not_produce():
    """CDC records carry Debezium's headers, not ours — consumers must cope."""
    assert env.decode_headers(None) == {}
    assert env.decode_headers([]) == {}
    # None-valued and non-UTF8 headers must not take a consumer down.
    assert env.decode_headers([("x", None), ("y", b"\xff\xfe"), ("z", b"ok")]) == {"z": "ok"}


def test_record_seq_of_ignores_malformed_values():
    assert env.record_seq_of({env.H_RECORD_SEQ: "not-a-number"}) is None
    assert env.record_seq_of({}) is None
