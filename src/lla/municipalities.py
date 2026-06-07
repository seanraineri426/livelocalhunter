"""Authoritative municipality checklist for the three pilot counties.

Names were pulled directly from each county's GIS so they match the polygon
layers we will later use to spatially assign a zoning district to each parcel:
  - Miami-Dade: MD_LandInformation/MapServer/19 (Municipal Zoning, MUNICNAME)
  - Broward:    services5 .../Cities/FeatureServer/0 (CITYNAME)
  - Palm Beach: maps.co.palm-beach.fl.us .../PZB/Municipalities (FNAME)

Each entry is (canonical_name, gis_name). `gis_name` is the exact string the
county GIS uses; `canonical_name` is the clean display/lookup name.
The unincorporated county itself is included as a jurisdiction so there are
no gaps in coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

from lla.config import COUNTY_FIPS


@dataclass(frozen=True)
class Municipality:
    name: str
    gis_name: str
    county_fips: str
    is_unincorporated: bool = False


# --- Miami-Dade (FIPS 12086): 34 incorporated cities + unincorporated ---
_MIAMI_DADE = [
    "Aventura", "Bal Harbour", "Bay Harbor Islands", "Biscayne Park",
    "Coral Gables", "Cutler Bay", "Doral", "El Portal", "Florida City",
    "Golden Beach", "Hialeah", "Hialeah Gardens", "Homestead",
    "Indian Creek Village", "Key Biscayne", "Medley", "Miami", "Miami Beach",
    "Miami Gardens", "Miami Lakes", "Miami Shores", "Miami Springs",
    "North Bay Village", "North Miami", "North Miami Beach", "Opa-locka",
    "Palmetto Bay", "Pinecrest", "South Miami", "Sunny Isles Beach",
    "Surfside", "Sweetwater", "Virginia Gardens", "West Miami",
]

# --- Broward (FIPS 12011): 31 incorporated cities + unincorporated (BMSD) ---
_BROWARD = [
    "Coconut Creek", "Cooper City", "Coral Springs", "Dania Beach", "Davie",
    "Deerfield Beach", "Fort Lauderdale", "Hallandale Beach", "Hillsboro Beach",
    "Hollywood", "Lauderdale-By-The-Sea", "Lauderdale Lakes", "Lauderhill",
    "Lazy Lake", "Lighthouse Point", "Margate", "Miramar", "North Lauderdale",
    "Oakland Park", "Parkland", "Pembroke Park", "Pembroke Pines", "Plantation",
    "Pompano Beach", "Sea Ranch Lakes", "Southwest Ranches", "Sunrise",
    "Tamarac", "West Park", "Weston", "Wilton Manors",
]

# Broward GIS uses ALL-CAPS CITYNAME; build gis_name by upper-casing the
# canonical name, with the two hyphenated exceptions handled explicitly.
_BROWARD_GIS_OVERRIDES = {
    "Lauderdale-By-The-Sea": "LAUDERDALE BY THE SEA",
}

# --- Palm Beach (FIPS 12099): 39 incorporated cities + unincorporated ---
# (canonical, gis_name) because PBC prefixes CITY OF / TOWN OF / VILLAGE OF.
_PALM_BEACH = [
    ("Atlantis", "CITY OF ATLANTIS"),
    ("Belle Glade", "CITY OF BELLE GLADE"),
    ("Boca Raton", "CITY OF BOCA RATON"),
    ("Boynton Beach", "CITY OF BOYNTON BEACH"),
    ("Delray Beach", "CITY OF DELRAY BEACH"),
    ("Greenacres", "CITY OF GREENACRES"),
    ("Lake Worth Beach", "CITY OF LAKE WORTH BEACH"),
    ("Pahokee", "CITY OF PAHOKEE"),
    ("Palm Beach Gardens", "CITY OF PALM BEACH GARDENS"),
    ("Riviera Beach", "CITY OF RIVIERA BEACH"),
    ("South Bay", "CITY OF SOUTH BAY"),
    ("West Palm Beach", "CITY OF WEST PALM BEACH"),
    ("Westlake", "CITY OF WESTLAKE"),
    ("Briny Breezes", "TOWN OF BRINY BREEZES"),
    ("Cloud Lake", "TOWN OF CLOUD LAKE"),
    ("Glen Ridge", "TOWN OF GLEN RIDGE"),
    ("Gulf Stream", "TOWN OF GULF STREAM"),
    ("Haverhill", "TOWN OF HAVERHILL"),
    ("Highland Beach", "TOWN OF HIGHLAND BEACH"),
    ("Hypoluxo", "TOWN OF HYPOLUXO"),
    ("Juno Beach", "TOWN OF JUNO BEACH"),
    ("Jupiter", "TOWN OF JUPITER"),
    ("Jupiter Inlet Colony", "TOWN OF JUPITER INLET COLONY"),
    ("Lake Clarke Shores", "TOWN OF LAKE CLARKE SHORES"),
    ("Lake Park", "TOWN OF LAKE PARK"),
    ("Lantana", "TOWN OF LANTANA"),
    ("Loxahatchee Groves", "TOWN OF LOXAHATCHEE GROVES"),
    ("Manalapan", "TOWN OF MANALAPAN"),
    ("Mangonia Park", "TOWN OF MANGONIA PARK"),
    ("Ocean Ridge", "TOWN OF OCEAN RIDGE"),
    ("Palm Beach", "TOWN OF PALM BEACH"),
    ("Palm Beach Shores", "TOWN OF PALM BEACH SHORES"),
    ("South Palm Beach", "TOWN OF SOUTH PALM BEACH"),
    ("Golf", "VILLAGE OF GOLF"),
    ("North Palm Beach", "VILLAGE OF NORTH PALM BEACH"),
    ("Palm Springs", "VILLAGE OF PALM SPRINGS"),
    ("Royal Palm Beach", "VILLAGE OF ROYAL PALM BEACH"),
    ("Tequesta", "VILLAGE OF TEQUESTA"),
    ("Wellington", "VILLAGE OF WELLINGTON"),
]


def all_municipalities() -> list[Municipality]:
    out: list[Municipality] = []

    md = COUNTY_FIPS["miami_dade"]
    for name in _MIAMI_DADE:
        out.append(Municipality(name, name.upper(), md))
    out.append(Municipality("Unincorporated Miami-Dade", "UNINCORPORATED", md, True))

    bc = COUNTY_FIPS["broward"]
    for name in _BROWARD:
        gis = _BROWARD_GIS_OVERRIDES.get(name, name.upper())
        out.append(Municipality(name, gis, bc))
    out.append(
        Municipality(
            "Unincorporated Broward (BMSD)",
            "BROWARD MUNICIPAL SERVICES DISTRICT",
            bc,
            True,
        )
    )

    pb = COUNTY_FIPS["palm_beach"]
    for name, gis in _PALM_BEACH:
        out.append(Municipality(name, gis, pb))
    out.append(
        Municipality("Unincorporated Palm Beach", "PALM BEACH COUNTY", pb, True)
    )

    return out


def summary() -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in all_municipalities():
        counts[m.county_fips] = counts.get(m.county_fips, 0) + 1
    return counts
