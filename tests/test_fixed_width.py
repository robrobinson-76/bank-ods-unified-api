"""Fixed-width parsing and wire-format decoding.

Core suite: pure functions, no infrastructure. These are the two places a
silent data-corruption bug could hide — a field read at the wrong offset, or a
zoned decimal decoded through a float — so they get direct coverage.
"""
from datetime import timedelta
from decimal import Decimal

import pytest

from ods_ingest.adapters.file import batches, fixed_width as fw
from ods_ingest.curation import decode


# ── Zoned (display) decimal ──────────────────────────────────────────────────

def test_zoned_decodes_the_documented_example():
    """RawCustodyPosition's docstring: PIC 9(12)V9(4) "…8505000" is 850.5."""
    assert decode.zoned_to_decimal("0000000008505000", 4) == Decimal("850.5")


def test_signed_overpunch_negative_documented_example():
    """"0000012345}" is -1234.50 — the } carries digit 0 and a negative sign."""
    assert decode.zoned_to_decimal("0000012345}", 2, signed=True) == Decimal("-1234.50")


def test_signed_overpunch_positive():
    # "{ABCDEFGHI" maps to digits 0-9, so "{" is 0 and "E" is 5.
    assert decode.zoned_to_decimal("0000012345{", 2, signed=True) == Decimal("1234.50")
    assert decode.zoned_to_decimal("0000012345E", 2, signed=True) == Decimal("1234.55")
    # And the negative table is positionally identical: "J" is 1, "R" is 9.
    assert decode.zoned_to_decimal("0000012345J", 2, signed=True) == Decimal("-1234.51")
    assert decode.zoned_to_decimal("0000012345R", 2, signed=True) == Decimal("-1234.59")


def test_signed_overpunch_all_digits_round_trip():
    for digit in range(10):
        pos = decode.zoned_to_decimal("00000000" + f"{digit}" + "0" + decode.OVERPUNCH_POS[digit],
                                      2, signed=True)
        neg = decode.zoned_to_decimal("00000000" + f"{digit}" + "0" + decode.OVERPUNCH_NEG[digit],
                                      2, signed=True)
        assert pos == -neg
        assert pos >= 0


def test_zoned_is_exact_not_floating_point():
    """A value float64 cannot hold must survive intact — this is money."""
    raw = decode.decimal_to_zoned(Decimal("12345678901.1234"), 12, 4)
    assert decode.zoned_to_decimal(raw, 4) == Decimal("12345678901.1234")


@pytest.mark.parametrize("value", ["0.0001", "-9999.99", "123456789.01", "0"])
def test_zoned_round_trip(value):
    dec = Decimal(value)
    raw = decode.decimal_to_zoned(dec, 12, 4, signed=True)
    assert decode.zoned_to_decimal(raw, 4, signed=True) == dec


@pytest.mark.parametrize("bad", ["", "   ", "12A45", "12345*"])
def test_zoned_rejects_malformed(bad):
    with pytest.raises(decode.DecodeError):
        decode.zoned_to_decimal(bad, 2)


# ── Dates ────────────────────────────────────────────────────────────────────

def test_ccyymmdd_parses_to_utc_midnight():
    dt = decode.ccyymmdd_to_datetime("20260730")
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 7, 30, 0)
    assert dt.utcoffset() == timedelta(0)


def test_julian_round_trip():
    dt = decode.ccyymmdd_to_datetime("20260730")
    assert decode.julian_to_datetime(decode.datetime_to_julian(dt)) == dt


def test_julian_handles_leap_day():
    # 2024 is a leap year: day 60 is 29 February.
    assert decode.julian_to_datetime("2024060") == decode.ccyymmdd_to_datetime("20240229")
    # 2026 is not: day 60 is 1 March.
    assert decode.julian_to_datetime("2026060") == decode.ccyymmdd_to_datetime("20260301")


def test_julian_rejects_day_366_in_a_common_year():
    with pytest.raises(decode.DecodeError):
        decode.julian_to_datetime("2026366")


@pytest.mark.parametrize("sentinel", ["", "00000000", "99999999", None])
def test_optional_date_treats_sentinels_as_absent(sentinel):
    assert decode.optional_date(sentinel) is None


@pytest.mark.parametrize("bad", ["2026-07-30", "20261332", "abc"])
def test_ccyymmdd_rejects_malformed(bad):
    with pytest.raises(decode.DecodeError):
        decode.ccyymmdd_to_datetime(bad)


# ── Layout ───────────────────────────────────────────────────────────────────

def test_every_layout_is_the_declared_record_length():
    for fields in (fw.HEADER_FIELDS, fw.DETAIL_FIELDS, fw.TRAILER_FIELDS):
        assert sum(f.length for f in fields) == fw.RECORD_LENGTH


def test_detail_layout_covers_every_raw_model_field():
    """The parser must produce exactly what the raw model declares.

    A field in the model with no layout entry would land as null forever; a
    layout field absent from the model would be silently dropped at the sink.
    """
    from bank_ods.models.raw_custody_position import RawCustodyPosition

    layout_names = {f.name for f in fw.DETAIL_FIELDS}
    model_names = set(RawCustodyPosition.model_fields)
    # REC_ID is assigned by the loader, not carried in the file.
    assert model_names - {"REC_ID"} == layout_names


def test_pack_parse_round_trip():
    values = {f.name: "" for f in fw.DETAIL_FIELDS}
    values.update({
        "POS_REC_TYPE": "03", "POS_BUS_DATE": "20260730", "POS_ACCT_NBR": "000000000007",
        "POS_CUSIP_NBR": "037833100", "POS_SEC_DESC": "APPLE INC", "POS_CCY_CD": "USD",
        "POS_SHR_QTY": "0000000008505000", "POS_MKT_VALUE": "000000000850500",
    })
    line = fw.pack(fw.DETAIL_FIELDS, values)
    assert len(line) == fw.RECORD_LENGTH
    parsed = fw.parse_detail(line)
    # Space fill is padding, not data.
    assert parsed["POS_SEC_DESC"] == "APPLE INC"
    assert parsed["POS_ACCT_NBR"] == "000000000007"
    assert parsed["POS_SHR_QTY"] == "0000000008505000"


def test_pack_refuses_to_truncate():
    """Silent truncation would corrupt an identifier; it must be an error."""
    with pytest.raises(fw.ParseError):
        fw.pack(fw.DETAIL_FIELDS, {"POS_CUSIP_NBR": "0378331009999"})


def test_parse_rejects_wrong_length_and_wrong_type():
    with pytest.raises(fw.ParseError):
        fw.parse_detail("03short")
    header_line = fw.pack(fw.HEADER_FIELDS, {"HDR_REC_TYPE": "01"})
    with pytest.raises(fw.ParseError):
        fw.parse_detail(header_line)


# ── Control totals ───────────────────────────────────────────────────────────

def _detail(qty: str, value: str) -> dict:
    return {"POS_SHR_QTY": qty, "POS_MKT_VALUE": value}


def test_control_totals_sum_exactly():
    details = [
        _detail("0000000000010000", "000000000010050"),  # 1.0 qty, 100.50 value
        _detail("0000000000025000", "000000000020025"),  # 2.5 qty, 200.25 value
    ]
    qty, value = batches.sum_control_totals(details)
    assert qty == Decimal("3.5")
    assert value == Decimal("300.75")


def test_rec_id_matches_the_documented_convention():
    """<POS_BUS_DATE>-<sequence>, so pipeline and seed keys are interchangeable."""
    assert batches.rec_id_for("20260730", 42) == "20260730-000042"
