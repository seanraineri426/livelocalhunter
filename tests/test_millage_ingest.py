from __future__ import annotations

from decimal import Decimal

from lla.millage_ingest import (
    _parse_broward_rows,
    _parse_miami_dade_rows,
    _parse_palm_beach_rows,
    jurisdiction_lookup_keys,
)


def test_miami_dade_doral_authority_split():
    line = (
        "3500 Doral 1.7166 0.4810 5.4990 1.0000 0.1340 0.0948 0.0327 0.1026 0.0288 "
        "4.5740 0.4171 2.3965 0.2812 0.4638 17.2221 17.2373"
    )
    rows = _parse_miami_dade_rows(
        line,
        source_url="https://example.test/md.pdf",
        source_method="test",
        tax_year=2025,
    )
    by_name = {row.authority_name: row for row in rows}
    assert by_name["Municipal Operating"].millage == Decimal("1.7166")
    assert by_name["School Board Operating"].millage == Decimal("5.4990")
    assert by_name["Miami-Dade County Operating"].millage == Decimal("4.5740")
    assert by_name["The Children's Trust"].millage == Decimal("0.4638")


def test_broward_tamarac_authority_split():
    line = "3112 Tamarac 5.6658 0.0000 6.3200 0.1645 0.4500 1.2391 0.0270 0.2301 7.0000 3112 21.0965 21.0965"
    rows = _parse_broward_rows(
        line,
        source_url="https://example.test/broward.pdf",
        source_method="test",
        tax_year=2025,
    )
    by_name = {row.authority_name: row for row in rows}
    assert by_name["Municipal Operating"].millage == Decimal("7.0000")
    assert by_name["School Board Operating"].millage == Decimal("6.3200")
    assert by_name["Broward County Operating"].millage == Decimal("5.6658")


def test_palm_beach_unincorporated_authority_split():
    line = (
        "00071 1991UNINCORPORATED COUNTY 4.5000 0.0330 3.4581 0.5491 0.0000 3.2480 3.0730 "
        "0.0948 0.1026 0.0327 0.4908 0.0270 0.6561 16.2652"
    )
    rows = _parse_palm_beach_rows(
        line,
        source_url="https://example.test/pbc.pdf",
        source_method="test",
        tax_year=2025,
    )
    by_name = {row.authority_name: row for row in rows}
    assert "Municipal Operating" not in by_name
    assert by_name["Palm Beach County Countywide Operating"].millage == Decimal("4.5000")
    assert by_name["Palm Beach County Fire Rescue MSTU"].millage == Decimal("3.4581")
    assert by_name["Palm Beach County School Board Required Local Effort"].millage == Decimal("3.2480")


def test_jurisdiction_lookup_keys_are_exact():
    keys = jurisdiction_lookup_keys("Unincorporated Miami-Dade")
    assert keys == {"unincorporated miami-dade"}
