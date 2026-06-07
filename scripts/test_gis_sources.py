#!/usr/bin/env python3
"""Smoke-test configured public GIS parcel sources."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.arcgis import fetch_features, layer_metadata, normalize_feature
from lla.gis_sources import PARCEL_SOURCES


def main() -> None:
    results = {}

    for county_key, source in PARCEL_SOURCES.items():
        metadata = layer_metadata(source)
        fields = {field["name"] for field in metadata.get("fields", [])}
        missing = [
            field
            for field in (
                source.id_field,
                source.acreage_field,
                source.lot_sf_field,
                source.zoning_field,
                source.use_field,
            )
            if field and field not in fields
        ]

        features = list(fetch_features(source, limit=2, page_size=2))
        normalized = [
            row
            for feature in features
            if (row := normalize_feature(source, feature)) is not None
        ]

        results[county_key] = {
            "name": source.name,
            "url": source.url,
            "geometry_type": metadata.get("geometryType"),
            "max_record_count": metadata.get("maxRecordCount"),
            "field_count": len(fields),
            "missing_configured_fields": missing,
            "feature_count": len(features),
            "sample": {
                key: value
                for key, value in (normalized[0] if normalized else {}).items()
                if key != "geometry"
            },
        }

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
