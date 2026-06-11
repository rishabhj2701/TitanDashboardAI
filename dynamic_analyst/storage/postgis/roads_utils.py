from __future__ import annotations

from typing import Dict, List, Optional


ROAD_ID_FIELDS = [
    "road_segment_id",
    "segment_id",
    "road_id",
    "roadid",
    "id",
    "gid",
    "fid",
    "ss_pavement_id",
    "pavement_id",
    "link_id",
    "linkid",
    "osm_id",
    "osmid",
    "objectid",
    "object_id",
    "feature_id",
    "featureid",
]

ROAD_NAME_FIELDS = [
    "name",
    "road_name",
    "roadname",
    "street_name",
    "streetname",
    "street",
    "travelway_name",
    "route_name",
    "routename",
    "full_name",
    "fullname",
    "rd_name",
    "rdname",
    "label",
    "road",
    "route",
    "highway",
]


def _detect_road_fields(columns: List[str]) -> Dict[str, Optional[str]]:
    columns_lower = {c.lower(): c for c in columns}

    id_field = None
    for candidate in ROAD_ID_FIELDS:
        if candidate in columns_lower:
            id_field = columns_lower[candidate]
            break

    name_field = None
    for candidate in ROAD_NAME_FIELDS:
        if candidate in columns_lower:
            name_field = columns_lower[candidate]
            break

    return {
        "id_field": id_field,
        "name_field": name_field,
    }

