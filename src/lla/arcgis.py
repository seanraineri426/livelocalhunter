from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import requests
from arcgis2geojson import arcgis2geojson
from sqlalchemy import text
from sqlalchemy.engine import Engine

from lla.gis_sources import ParcelSource


class ArcGISError(RuntimeError):
    pass


def _request(url: str, params: dict[str, Any], *, timeout: int, retries: int) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("error"):
                raise ArcGISError(f"{url}: {payload['error']}")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise ArcGISError(f"Request failed after {retries} attempts: {url}") from last_exc


def layer_metadata(source: ParcelSource, *, timeout: int = 30) -> dict[str, Any]:
    return _request(source.url, {"f": "json"}, timeout=timeout, retries=3)


def fetch_features(
    source: ParcelSource,
    *,
    limit: int,
    offset: int = 0,
    page_size: int = 500,
    timeout: int = 60,
    retries: int = 3,
) -> Iterator[dict[str, Any]]:
    """Yield GeoJSON features from an ArcGIS REST layer.

    Uses Esri JSON (``f=json``) plus arcgis2geojson conversion so it works against
    services that do not emit GeoJSON directly (e.g. older county MapServers).
    """
    fetched = 0
    out_fields = ",".join(source.out_fields)
    query_url = f"{source.url}/query"

    while fetched < limit:
        count = min(page_size, limit - fetched)
        params = {
            "f": "json",
            "where": source.where,
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": source.order_by,
            "resultOffset": offset + fetched,
            "resultRecordCount": count,
        }
        payload = _request(query_url, params, timeout=timeout, retries=retries)
        esri_features = payload.get("features", [])
        if not esri_features:
            return

        for esri_feature in esri_features:
            if not esri_feature.get("geometry"):
                continue
            yield arcgis2geojson(esri_feature)

        fetched += len(esri_features)
        if not payload.get("exceededTransferLimit") and len(esri_features) < count:
            return


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive(value: Any) -> float | None:
    num = _number(value)
    if num is None or num <= 0:
        return None
    return num


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value or text_value.upper() in {"NULL", "NONE", "N/A", "NA"}:
        return None
    return text_value


def _clean_zip(value: Any) -> str | None:
    text_value = _clean_text(value)
    if text_value is None:
        return None
    # Some sources store ZIP as a float (e.g. 33101.0); keep the leading 5 digits.
    digits = "".join(ch for ch in text_value if ch.isdigit())
    if not digits:
        return None
    return digits[:5]


def extract_site_address(source: ParcelSource, props: dict[str, Any]) -> dict[str, Any]:
    """Pull the physical site address fields configured for a parcel source."""

    return {
        "site_address": _clean_text(props.get(source.site_address_field)) if source.site_address_field else None,
        "site_city": _clean_text(props.get(source.site_city_field)) if source.site_city_field else None,
        "site_zip": _clean_zip(props.get(source.site_zip_field)) if source.site_zip_field else None,
    }


def normalize_feature(source: ParcelSource, feature: dict[str, Any]) -> dict[str, Any] | None:
    props = feature.get("properties") or {}
    parcel_id = props.get(source.id_field)
    geometry = feature.get("geometry")
    if not parcel_id or not geometry:
        return None

    acreage = _positive(props.get(source.acreage_field)) if source.acreage_field else None
    lot_sf = _positive(props.get(source.lot_sf_field)) if source.lot_sf_field else None
    if acreage is None and lot_sf is not None:
        acreage = lot_sf / 43560
    if lot_sf is None and acreage is not None:
        lot_sf = acreage * 43560

    address = extract_site_address(source, props)

    return {
        "county_fips": source.county_fips,
        "source_parcel_id": str(parcel_id),
        "source_parcel_id_normalized": "".join(ch for ch in str(parcel_id) if ch.isalnum()),
        "geometry": json.dumps(geometry),
        "acreage": acreage,
        "lot_sf": lot_sf,
        "zoning_code": props.get(source.zoning_field) if source.zoning_field else None,
        "use_class": props.get(source.use_field) if source.use_field else None,
        "is_candidate": False,
        "candidate_bucket": None,
        "candidate_reason": None,
        "normalized_use": None,
        "source": f"arcgis:{source.county_key}",
        "site_address": address["site_address"],
        "site_city": address["site_city"],
        "site_zip": address["site_zip"],
        "address_source": f"arcgis:{source.county_key}" if address["site_address"] else None,
    }


UPSERT_SQL = text(
    """
    INSERT INTO lla.parcels (
        county_fips,
        source_parcel_id,
        source_parcel_id_normalized,
        geom,
        acreage,
        lot_sf,
        zoning_code,
        use_class,
        is_candidate,
        candidate_bucket,
        candidate_reason,
        normalized_use,
        source,
        site_address,
        site_city,
        site_zip,
        address_source,
        address_updated_at,
        as_of_date,
        updated_at
    )
    VALUES (
        :county_fips,
        :source_parcel_id,
        :source_parcel_id_normalized,
        ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)),
        :acreage,
        :lot_sf,
        :zoning_code,
        :use_class,
        :is_candidate,
        :candidate_bucket,
        :candidate_reason,
        :normalized_use,
        :source,
        :site_address,
        :site_city,
        :site_zip,
        :address_source,
        CASE WHEN :site_address IS NOT NULL THEN now() ELSE NULL END,
        CURRENT_DATE,
        now()
    )
    ON CONFLICT (county_fips, source_parcel_id)
    DO UPDATE SET
        source_parcel_id_normalized = EXCLUDED.source_parcel_id_normalized,
        geom = EXCLUDED.geom,
        acreage = EXCLUDED.acreage,
        lot_sf = EXCLUDED.lot_sf,
        zoning_code = EXCLUDED.zoning_code,
        use_class = EXCLUDED.use_class,
        is_candidate = EXCLUDED.is_candidate,
        candidate_bucket = EXCLUDED.candidate_bucket,
        candidate_reason = EXCLUDED.candidate_reason,
        normalized_use = EXCLUDED.normalized_use,
        source = EXCLUDED.source,
        -- Preserve a previously captured address if this re-ingest lacks one.
        site_address = COALESCE(EXCLUDED.site_address, lla.parcels.site_address),
        site_city = COALESCE(EXCLUDED.site_city, lla.parcels.site_city),
        site_zip = COALESCE(EXCLUDED.site_zip, lla.parcels.site_zip),
        address_source = COALESCE(EXCLUDED.address_source, lla.parcels.address_source),
        address_updated_at = CASE
            WHEN EXCLUDED.site_address IS NOT NULL THEN now()
            ELSE lla.parcels.address_updated_at
        END,
        as_of_date = EXCLUDED.as_of_date,
        updated_at = now()
    """
)


def upsert_parcels(engine: Engine, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    with engine.begin() as conn:
        conn.execute(UPSERT_SQL, rows)
    return len(rows)
