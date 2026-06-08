"""Parse and normalize pilot-county millage tables for Supabase ingestion."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

import pdfplumber
import requests

from lla.config import COUNTY_FIPS
from lla.firecrawl import FirecrawlClient, FirecrawlError


COUNTY_NAMES = {
    "12086": "Miami-Dade County",
    "12011": "Broward County",
    "12099": "Palm Beach County",
}

FHC_OPT_OUT_SUMMARY_URL = (
    "https://flhousing.org/wp-content/uploads/2024/03/"
    "FHC-Summary.-New-Multifamily-Middle-Market-Tax-Exemption-Opt-out-3.8.24.pdf"
)

COUNTY_CANNOT_OPT_OUT = {
    "12086": {
        "county_has_adequate_supply": True,
        "opt_out_source_url": FHC_OPT_OUT_SUMMARY_URL,
        "note": (
            "Florida Housing Coalition lists Miami-Dade County taxing authorities as not "
            "eligible to opt out of the 80-120% AMI tier based on Shimberg adequate-supply "
            "analysis. This is not a recorded local opt-out vote."
        ),
    },
    "12011": {
        "county_has_adequate_supply": True,
        "opt_out_source_url": FHC_OPT_OUT_SUMMARY_URL,
        "note": (
            "Florida Housing Coalition lists Broward County taxing authorities as not "
            "eligible to opt out of the 80-120% AMI tier based on Shimberg adequate-supply "
            "analysis. This is not a recorded local opt-out vote."
        ),
    },
    "12099": {
        "county_has_adequate_supply": True,
        "opt_out_source_url": FHC_OPT_OUT_SUMMARY_URL,
        "note": (
            "Florida Housing Coalition lists Palm Beach County taxing authorities as not "
            "eligible to opt out of the 80-120% AMI tier based on Shimberg adequate-supply "
            "analysis. This is not a recorded local opt-out vote."
        ),
    },
}

MUNICIPALITY_ALIASES = {
    "bay harbor island": "Bay Harbor Islands",
    "indian creek": "Indian Creek Village",
    "sunny isles": "Sunny Isles Beach",
    "miami (dda)": "Miami",
    "gulfstream": "Gulf Stream",
    "jupiter inlet beach": "Jupiter Inlet Colony",
    "lake clark shores": "Lake Clarke Shores",
    "glenridge": "Glen Ridge",
    "uninc. county": "Unincorporated Miami-Dade",
    "unincorporated - n hospital": "Unincorporated Broward (BMSD)",
    "unincorporated - s hospital": "Unincorporated Broward (BMSD)",
    "unincorporated county": "Unincorporated Palm Beach",
    "ft lauderdale": "Fort Lauderdale",
    "ft lauderdale - hillsboro inlet": "Fort Lauderdale",
    "ft lauderdale dda": "Fort Lauderdale",
    "pt lauderdale": "Fort Lauderdale",
    "daniel beach": "Dania Beach",
    "dania beach - n hosp": "Dania Beach",
    "dania beach - s hosp": "Dania Beach",
    "oakland beach": "Oakland Park",
    "miami north": "North Miami",
    "miami beach": "Miami Beach",
    "lake worth (o.e.)": "Lake Worth Beach",
    "lake worth (o.s.)": "Lake Worth Beach",
    "detry beach (o.e.)": "Delray Beach",
    "detry beach (o.s.)": "Delray Beach",
    "boost raton (o.e.)": "Boca Raton",
    "boyton beach (o.e.)": "Boynton Beach",
    "puget sound (o.e.)": "Pahokee",
}


@dataclass(frozen=True)
class MillageAuthorityRow:
    county_fips: str
    jurisdiction_name: str
    authority_name: str
    authority_type: str
    millage: Decimal
    tax_year: int
    millage_source_url: str
    source_method: str
    effective_date: str | None = None
    opted_out_middle: bool | None = None
    county_has_adequate_supply: bool | None = None
    opt_out_source_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _decimal(value: float | str | Decimal) -> Decimal:
    return Decimal(str(value))


def _normalize_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.strip())
    cleaned = re.sub(r"^\d+\s*", "", cleaned)
    cleaned = re.sub(r"\s+\d+\.\d{3,4}.*$", "", cleaned).strip()
    lowered = cleaned.lower()
    if lowered in MUNICIPALITY_ALIASES:
        return MUNICIPALITY_ALIASES[lowered]
    return cleaned


def _fetch_pdf_text(url: str, *, prefer_firecrawl: bool) -> tuple[str, str]:
    if prefer_firecrawl:
        try:
            client = FirecrawlClient(timeout=120)
            markdown = client.scrape(url, formats=["markdown"], only_main_content=False).get("markdown") or ""
            if markdown and re.search(r"\d+\.\d{3,4}", markdown):
                return markdown, "firecrawl_scrape"
        except (FirecrawlError, RuntimeError):
            pass

    response = requests.get(url, timeout=90, headers={"User-Agent": "LiveLocalHunter/1.0 millage-ingest"})
    response.raise_for_status()
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    return text, "direct_pdf"


def _authority_rows(
  *,
  county_fips: str,
  jurisdiction_name: str,
  tax_year: int,
  source_url: str,
  source_method: str,
  authorities: list[tuple[str, str, Decimal]],
  raw: dict[str, Any],
) -> list[MillageAuthorityRow]:
    county_context = COUNTY_CANNOT_OPT_OUT.get(county_fips, {})
    rows: list[MillageAuthorityRow] = []
    for authority_name, authority_type, millage in authorities:
        if millage <= 0:
            continue
        rows.append(
            MillageAuthorityRow(
                county_fips=county_fips,
                jurisdiction_name=jurisdiction_name,
                authority_name=authority_name,
                authority_type=authority_type,
                millage=millage,
                tax_year=tax_year,
                millage_source_url=source_url,
                source_method=source_method,
                effective_date=f"{tax_year}-11-01",
                opted_out_middle=None,
                county_has_adequate_supply=county_context.get("county_has_adequate_supply"),
                opt_out_source_url=county_context.get("opt_out_source_url"),
                raw={
                    **raw,
                    "adequate_supply_note": county_context.get("note"),
                    "opt_out_status": "unknown_unless_verified_local_resolution",
                },
            )
        )
    return rows


def _parse_miami_dade_rows(text: str, *, source_url: str, source_method: str, tax_year: int) -> list[MillageAuthorityRow]:
    rows: list[MillageAuthorityRow] = []
    pattern = re.compile(
        r"^(?P<code>\d{4})\s+(?P<name>[A-Za-z0-9\.\s&'()/-]+?)\s+(?P<nums>(?:\d+\.\d{4}\s*)+)$"
    )
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        nums = [float(value) for value in re.findall(r"\d+\.\d{4}", match.group("nums"))]
        if len(nums) < 14:
            continue
        total_millage = nums[14] if len(nums) > 14 else nums[-1]
        jurisdiction_name = _normalize_name(match.group("name"))
        authorities = [
            ("Municipal Operating", "municipal", _decimal(nums[0])),
            ("Municipal Debt Service", "municipal", _decimal(nums[1])),
            ("School Board Operating", "school", _decimal(nums[2])),
            ("School Board Debt Service", "school", _decimal(nums[3])),
            ("Everglades Restoration", "special", _decimal(nums[4])),
            ("Okeechobee Basin", "special", _decimal(nums[5])),
            ("Florida Inland Navigation District", "special", _decimal(nums[6])),
            ("South Florida Water Management District", "special", _decimal(nums[7])),
            ("Lake Okeechobee Basin", "special", _decimal(nums[8])),
            ("Miami-Dade County Operating", "county", _decimal(nums[9])),
            ("Miami-Dade County Debt Service", "county", _decimal(nums[10])),
            ("Miami-Dade Fire Rescue", "special", _decimal(nums[11])),
            ("Miami-Dade Library", "special", _decimal(nums[12])),
            ("The Children's Trust", "special", _decimal(nums[13])),
        ]
        rows.extend(
            _authority_rows(
                county_fips="12086",
                jurisdiction_name=jurisdiction_name,
                tax_year=tax_year,
                source_url=source_url,
                source_method=source_method,
                authorities=authorities,
                raw={"source_line": line.strip(), "millage_code": match.group("code"), "total_millage": total_millage},
            )
        )
    return rows


def _parse_broward_rows(text: str, *, source_url: str, source_method: str, tax_year: int) -> list[MillageAuthorityRow]:
    rows: list[MillageAuthorityRow] = []
    merged_lines: list[str] = []
    pending_name = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\d{4}\s+", line):
            if pending_name:
                merged_lines.append(f"{pending_name} {line}")
            else:
                merged_lines.append(line)
            pending_name = ""
            continue
        if re.search(r"\d+\.\d{4}", line):
            merged_lines.append(line)
            continue
        pending_name = line

    prefixed_pattern = re.compile(
        r"^(?P<prefix>[A-Za-z0-9\.\s&'()/-]+)\s+(?P<code>\d{4})\s+"
        r"(?P<nums>(?:\d+\.\d{4}\s*){9,11})"
        r"(?:(?P<code2>\d{4})\s+)?(?P<total>\d+\.\d{4})(?:\s+(?P<dup>\d+\.\d{4}))?$"
    )
    standard_pattern = re.compile(
        r"^(?P<code>\d{4})\s+(?P<name>[A-Za-z0-9\.\s&'()/-]+?)\s+"
        r"(?P<nums>(?:\d+\.\d{4}\s*){9,11})"
        r"(?:(?P<code2>\d{4})\s+)?(?P<total>\d+\.\d{4})(?:\s+(?P<dup>\d+\.\d{4}))?$"
    )
    for line in merged_lines:
        match = prefixed_pattern.search(line) or standard_pattern.search(line)
        if not match:
            continue
        nums = [float(value) for value in re.findall(r"\d+\.\d{4}", match.group("nums"))]
        if len(nums) < 9:
            continue
        if match.groupdict().get("name"):
            jurisdiction_name = _normalize_name(match.group("name"))
        elif match.groupdict().get("prefix"):
            jurisdiction_name = _normalize_name(match.group("prefix"))
        else:
            continue

        city_operating = nums[8]
        city_debt = nums[9] if len(nums) >= 10 else Decimal("0")
        authorities = [
            ("Broward County Operating", "county", _decimal(nums[0])),
            ("Broward County Debt Service", "county", _decimal(nums[1])),
            ("School Board Operating", "school", _decimal(nums[2])),
            ("School Board Debt Service", "school", _decimal(nums[3])),
            ("Children's Services Council", "special", _decimal(nums[4])),
            ("North Broward Hospital District", "special", _decimal(nums[5])),
            ("Florida Inland Navigation District", "special", _decimal(nums[6])),
            ("South Florida Water Management District", "special", _decimal(nums[7])),
            ("Municipal Operating", "municipal", _decimal(city_operating)),
            ("Municipal Debt Service", "municipal", _decimal(city_debt)),
        ]
        rows.extend(
            _authority_rows(
                county_fips="12011",
                jurisdiction_name=jurisdiction_name,
                tax_year=tax_year,
                source_url=source_url,
                source_method=source_method,
                authorities=authorities,
                raw={"source_line": line.strip(), "millage_code": match.group("code"), "total_millage": match.group("total")},
            )
        )
    return rows


def _parse_palm_beach_components(nums: list[float]) -> list[tuple[str, str, Decimal]]:
    if len(nums) < 7:
        return []
    total = nums[-1]
    regional_tail = nums[-7:-1]
    rest = nums[:-7]
    index = 0
    components: list[tuple[str, str, Decimal]] = []

    def take(name: str, authority_type: str, expected: float | None = None) -> None:
        nonlocal index
        if index >= len(rest):
            return
        value = rest[index]
        if expected is not None and abs(value - expected) > 0.0002:
            return
        if value > 0:
            components.append((name, authority_type, _decimal(value)))
        index += 1

    take("Palm Beach County Countywide Operating", "county", 4.5)
    take("Palm Beach County Countywide Debt", "county", 0.033)
    if index < len(rest) and abs(rest[index] - 3.4581) < 0.0002:
        take("Palm Beach County Fire Rescue MSTU", "special", 3.4581)
        take("Palm Beach County Library District", "special", 0.5491)
    while index < len(rest) and rest[index] == 0:
        index += 1
    take("Palm Beach County School Board Required Local Effort", "school", 3.248)
    take("Palm Beach County School Board Discretionary Operating", "school", 3.073)

    middle = rest[index:]
    if middle:
        if middle[0] > 0:
            components.append(("Municipal Operating", "municipal", _decimal(middle[0])))
        for offset, value in enumerate(middle[1:], start=1):
            if value > 0:
                components.append((f"Municipal Special District {offset}", "special", _decimal(value)))

    tail_names = [
        ("South Florida Water Management District", "special"),
        ("Florida Inland Navigation District", "special"),
        ("Lake Okeechobee Basin", "special"),
        ("Local Discretionary", "special"),
        ("Independent District", "special"),
        ("County Special District", "special"),
    ]
    for (name, authority_type), value in zip(tail_names, regional_tail):
        if value > 0:
            components.append((name, authority_type, _decimal(value)))

    if not components and total > 0:
        components.append(("Total Millage", "special", _decimal(total)))
    return components


def _parse_palm_beach_rows(text: str, *, source_url: str, source_method: str, tax_year: int) -> list[MillageAuthorityRow]:
    rows: list[MillageAuthorityRow] = []
    pattern = re.compile(
        r"^(?P<taxauth>\d{5})\s+(?:(?P<pcn>\d+)\s*)?(?P<name>[A-Z][A-Z0-9 \.'()-]+?)\s+(?P<nums>(?:\d+\.\d{3,4}\s*)+)$"
    )
    grouped: dict[str, list[tuple[list[float], str]]] = {}
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        nums = [float(value) for value in re.findall(r"\d+\.\d{3,4}", match.group("nums"))]
        if len(nums) < 7:
            continue
        jurisdiction_name = _normalize_name(match.group("name"))
        grouped.setdefault(jurisdiction_name, []).append((nums, line.strip()))

    def _variant_score(nums: list[float]) -> tuple[int, bool, int, int]:
        components = _parse_palm_beach_components(nums)
        by_name = {component[0] for component in components}
        municipal_total = sum(float(component[2]) for component in components if component[1] == "municipal")
        missing_core = sum(
            1
            for required in (
                "Palm Beach County Countywide Operating",
                "Palm Beach County Fire Rescue MSTU",
                "Palm Beach County Library District",
            )
            if required not in by_name
        )
        return (missing_core, municipal_total > 0, -len(components), len(nums))

    for jurisdiction_name, variants in grouped.items():
        if jurisdiction_name == "Unincorporated Palm Beach":
            preferred = [
                item
                for item in variants
                if abs(item[0][-1] - 16.2652) < 0.0003 and len(item[0]) >= 14
            ]
            if preferred:
                variants = preferred
        nums, source_line = min(variants, key=lambda item: _variant_score(item[0]))
        authorities = _parse_palm_beach_components(nums)
        rows.extend(
            _authority_rows(
                county_fips="12099",
                jurisdiction_name=jurisdiction_name,
                tax_year=tax_year,
                source_url=source_url,
                source_method=source_method,
                authorities=authorities,
                raw={"source_line": source_line, "variant_count": len(variants), "total_millage": nums[-1]},
            )
        )
    return rows


COUNTY_SOURCES: dict[str, dict[str, Any]] = {
    "12086": {
        "tax_year": 2025,
        "url": "https://www.miamidadepa.gov/resources-pa/library/reports/millage/2025-proposed-millage-rate-table.pdf",
        "parser": _parse_miami_dade_rows,
    },
    "12011": {
        "tax_year": 2025,
        "url": "https://bcpa.net/Includes/Downloads/2025/2025%20Final%20Millage%20Rate%20Table.pdf",
        "parser": _parse_broward_rows,
    },
    "12099": {
        "tax_year": 2025,
        "url": "https://pbcpao.gov/pdf/taxroll/Palm_Beach_County_Tax_Auth_Code_Description.pdf",
        "parser": _parse_palm_beach_rows,
    },
}


def fetch_county_millage_text(county_fips: str, *, prefer_firecrawl: bool) -> tuple[str, str, str, int]:
    config = COUNTY_SOURCES[county_fips]
    text, method = _fetch_pdf_text(config["url"], prefer_firecrawl=prefer_firecrawl)
    return text, method, config["url"], int(config["tax_year"])


def parse_county_millage_rows(
    county_fips: str,
    text: str,
    *,
    source_method: str,
    source_url: str,
    tax_year: int,
) -> list[MillageAuthorityRow]:
    parser: Callable[..., list[MillageAuthorityRow]] = COUNTY_SOURCES[county_fips]["parser"]
    return parser(text, source_url=source_url, source_method=source_method, tax_year=tax_year)


def load_pilot_county_millage_rows(*, prefer_firecrawl: bool = True) -> list[MillageAuthorityRow]:
    rows: list[MillageAuthorityRow] = []
    for county_fips in COUNTY_FIPS.values():
        source_url = COUNTY_SOURCES[county_fips]["url"]
        tax_year = int(COUNTY_SOURCES[county_fips]["tax_year"])
        text, method = _fetch_pdf_text(source_url, prefer_firecrawl=prefer_firecrawl)
        parsed = parse_county_millage_rows(
            county_fips,
            text,
            source_method=method,
            source_url=source_url,
            tax_year=tax_year,
        )
        if not parsed:
            text, fallback_method = _fetch_pdf_text(source_url, prefer_firecrawl=False)
            method = f"{method}+{fallback_method}_fallback"
            parsed = parse_county_millage_rows(
                county_fips,
                text,
                source_method=method,
                source_url=source_url,
                tax_year=tax_year,
            )
        if not parsed:
            raise RuntimeError(f"No millage rows parsed for county {county_fips} from {source_url}")
        rows.extend(parsed)
    return rows


def jurisdiction_lookup_keys(name: str) -> set[str]:
    return {_normalize_name(name).lower()}
