from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParcelSource:
    county_key: str
    county_fips: str
    name: str
    url: str
    where: str
    id_field: str
    order_by: str
    acreage_field: str | None = None
    lot_sf_field: str | None = None
    zoning_field: str | None = None
    use_field: str | None = None
    use_desc_field: str | None = None
    out_fields: tuple[str, ...] = ("*",)
    # geometry_only sources have polygons but lack engine attributes (use/lot/zoning)
    # and require a downstream attribute join before they are useful.
    geometry_only: bool = False
    notes: str = ""


PARCEL_SOURCES: dict[str, ParcelSource] = {
    "miami_dade": ParcelSource(
        county_key="miami_dade",
        county_fips="12086",
        name="Miami-Dade Property Appraiser parcels (MD_LandInformation/26)",
        url="https://gisweb.miamidade.gov/arcgis/rest/services/MD_LandInformation/MapServer/26",
        where="FOLIO<>'0000000000000'",
        id_field="FOLIO",
        order_by="FOLIO ASC",
        acreage_field=None,
        lot_sf_field="LOT_SIZE",
        zoning_field="PRIMARY_ZONE",
        use_field="DOR_CODE_CUR",
        use_desc_field="DOR_DESC",
        out_fields=(
            "FOLIO",
            "LOT_SIZE",
            "PRIMARY_ZONE",
            "DOR_CODE_CUR",
            "DOR_DESC",
            "TRUE_SITE_ADDR",
            "TRUE_OWNER1",
        ),
    ),
    "broward": ParcelSource(
        county_key="broward",
        county_fips="12011",
        name="Broward County parcels (BCPA hosted, NAL-joined)",
        url="https://services5.arcgis.com/wI5GZmCtnUU8ueya/ArcGIS/rest/services/Broward_County_Parcel_Boundary/FeatureServer/1",
        where="PARCELNO IS NOT NULL",
        id_field="PARCELNO",
        order_by="PARCELNO ASC",
        acreage_field=None,
        lot_sf_field="LND_SQFOOT",
        zoning_field=None,
        use_field="DOR_UC",
        out_fields=(
            "PARCELNO",
            "DOR_UC",
            "LND_SQFOOT",
            "OWN_NAME",
            "PHY_ADDR1",
            "PHY_CITY",
        ),
        notes="Hosted BCPA parcel polygons joined to the FDOR NAL roll. DOR_UC = use code; no local zoning field.",
    ),
    "palm_beach": ParcelSource(
        county_key="palm_beach",
        county_fips="12099",
        name="Palm Beach County parcels (CollectionDays_Parcels, property-joined)",
        url="https://services1.arcgis.com/zsFpTOq1dFgzIjQg/arcgis/rest/services/CollectionDays_Parcels/FeatureServer/21",
        where="PCN IS NOT NULL",
        id_field="PCN",
        order_by="PCN ASC",
        acreage_field="ACRES",
        lot_sf_field=None,
        zoning_field=None,
        use_field="PROPERTY_USE",
        out_fields=(
            "PCN",
            "PROPERTY_USE",
            "ACRES",
            "OWNER_NAME1",
            "SITE_ADDR_STR",
            "MUNICIPALITY",
        ),
        notes="Hosted PBC parcel polygons joined to property info. PROPERTY_USE is text; ACRES is often 0 and can be backfilled from geometry.",
    ),
}


def get_source(county_key: str) -> ParcelSource:
    try:
        return PARCEL_SOURCES[county_key]
    except KeyError as exc:
        valid = ", ".join(sorted(PARCEL_SOURCES))
        raise ValueError(f"Unknown county '{county_key}'. Valid options: {valid}") from exc
