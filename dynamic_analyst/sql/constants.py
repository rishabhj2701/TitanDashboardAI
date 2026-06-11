"""Shared SQL constants and base column maps."""

from __future__ import annotations

from ..config import CRASH_TIMEZONE

TRAFFIC_MAP_LIMIT_DEFAULT = 5000
TRAFFIC_MAP_LIMIT_MAX = 5000
TRAFFIC_MAP_LIMIT_NEAR_MAX = 2500
TRAFFIC_NEAR_CRASH_OVERLAY_MAX = 1200
TRAFFIC_NEAR_WORKZONE_OVERLAY_MAX = 800
TRAFFIC_NEAR_COMBINED_MAX = 3200
TRAFFIC_MAP_QUERY_TIMEOUT_MS = 9000
TRAFFIC_NEAR_OVERLAY_QUERY_TIMEOUT_MS = 9000
TRAFFIC_SQL_RESULT_TIMEOUT_MS = 60000
TRAFFIC_RESULT_LIMIT_WITH_MAP = 200

DRIVABLE_HIGHWAY_TAGS: tuple[str, ...] = (
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "residential",
    "unclassified",
    "living_street",
    "service",
    "road",
)

NON_DRIVABLE_HINT_TERMS: tuple[str, ...] = (
    "trail",
    "footway",
    "path",
    "cycleway",
    "pedestrian",
    "track",
    "bridleway",
    "steps",
)

# Map user-facing column names -> SQL expressions (traffic/CV)
_SQL_COL = {
    "lat": "p.lat",
    "latitude": "p.lat",
    "lon": "p.lon",
    "longitude": "p.lon",
    "ts": "p.ts",
    "time": "p.ts",
    "timestamp": "p.ts",
    "road_segment_id": "p.road_segment_id",
    "road": "COALESCE(NULLIF(p.attrs->>'road',''), NULLIF(p.attrs->>'RoadName',''), NULLIF(p.attrs->>'roadName',''), NULLIF(p.attrs->>'road_name',''), r.name)",
    "road_name": "COALESCE(NULLIF(p.attrs->>'road',''), NULLIF(p.attrs->>'RoadName',''), NULLIF(p.attrs->>'roadName',''), NULLIF(p.attrs->>'road_name',''), r.name)",
    "name": "COALESCE(NULLIF(p.attrs->>'road',''), NULLIF(p.attrs->>'RoadName',''), NULLIF(p.attrs->>'roadName',''), NULLIF(p.attrs->>'road_name',''), r.name)",
    "speed": "COALESCE(NULLIF(p.attrs->>'speed','')::float8, NULLIF(p.attrs->>'SpeedMPH','')::float8, NULLIF(p.attrs->>'speed_mph','')::float8, NULLIF(p.attrs->>'speedMPH','')::float8)",
    "speedLimit": "COALESCE(NULLIF(p.attrs->>'SpeedLimitMPH','')::float8, NULLIF(p.attrs->>'speedLimit','')::float8, NULLIF(p.attrs->>'speed_limit_mph','')::float8, NULLIF(p.attrs->>'speed_limit','')::float8, NULLIF(p.attrs->>'SpeedLimit','')::float8)",
    "SpeedLimitMPH": "COALESCE(NULLIF(p.attrs->>'SpeedLimitMPH','')::float8, NULLIF(p.attrs->>'speedLimit','')::float8, NULLIF(p.attrs->>'speed_limit_mph','')::float8, NULLIF(p.attrs->>'speed_limit','')::float8, NULLIF(p.attrs->>'SpeedLimit','')::float8)",
    "speed_over_limit": "(\n        COALESCE(NULLIF(p.attrs->>'speed','')::float8, NULLIF(p.attrs->>'SpeedMPH','')::float8, NULLIF(p.attrs->>'speed_mph','')::float8, NULLIF(p.attrs->>'speedMPH','')::float8)\n        - COALESCE(NULLIF(p.attrs->>'SpeedLimitMPH','')::float8, NULLIF(p.attrs->>'speedLimit','')::float8, NULLIF(p.attrs->>'speed_limit_mph','')::float8, NULLIF(p.attrs->>'speed_limit','')::float8, NULLIF(p.attrs->>'SpeedLimit','')::float8)\n    )",
    "local_hour": f"EXTRACT(HOUR FROM (p.ts AT TIME ZONE '{CRASH_TIMEZONE}'))",
    "local_time": f"(p.ts AT TIME ZONE '{CRASH_TIMEZONE}')::time",
    "acc_x": "CASE WHEN NULLIF(p.attrs->>'AccX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccX')::float8 END",
    "acc_y": "CASE WHEN NULLIF(p.attrs->>'AccY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccY')::float8 END",
    "AccX": "CASE WHEN NULLIF(p.attrs->>'AccX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccX')::float8 END",
    "AccY": "CASE WHEN NULLIF(p.attrs->>'AccY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccY')::float8 END",
    "hp_acc_image_no": "NULLIF(p.attrs->>'hp_acc_image_no','')",
    "accident_id": "NULLIF(p.attrs->>'accident_id','')",
    "primary_id": "NULLIF(p.attrs->>'primary_id','')",
    "vehicle_id": "NULLIF(p.attrs->>'VehicleID','')",
}

# Map user-facing column names -> SQL expressions (traffic/CV hard braking table)
_HB_SQL_COL = {
    "lat": "p.lat",
    "latitude": "p.lat",
    "lon": "p.lon",
    "longitude": "p.lon",
    "ts": "p.ts",
    "time": "p.ts",
    "timestamp": "p.ts",
    "road_segment_id": "p.road_segment_id",
    "road": "COALESCE(NULLIF(p.attrs->>'road',''), NULLIF(p.attrs->>'RoadName',''), NULLIF(p.attrs->>'roadName',''), NULLIF(p.attrs->>'road_name',''), r.name)",
    "road_name": "COALESCE(NULLIF(p.attrs->>'road',''), NULLIF(p.attrs->>'RoadName',''), NULLIF(p.attrs->>'roadName',''), NULLIF(p.attrs->>'road_name',''), r.name)",
    "name": "COALESCE(NULLIF(p.attrs->>'road',''), NULLIF(p.attrs->>'RoadName',''), NULLIF(p.attrs->>'roadName',''), NULLIF(p.attrs->>'road_name',''), r.name)",
    "speed": "p.speed",
    "speedLimit": "COALESCE(p.speed_limit, NULLIF(p.attrs->>'SpeedLimitMPH','')::float8, NULLIF(p.attrs->>'speedLimit','')::float8, NULLIF(p.attrs->>'speed_limit_mph','')::float8, NULLIF(p.attrs->>'speed_limit','')::float8, NULLIF(p.attrs->>'SpeedLimit','')::float8)",
    "SpeedLimitMPH": "COALESCE(p.speed_limit, NULLIF(p.attrs->>'SpeedLimitMPH','')::float8, NULLIF(p.attrs->>'speedLimit','')::float8, NULLIF(p.attrs->>'speed_limit_mph','')::float8, NULLIF(p.attrs->>'speed_limit','')::float8, NULLIF(p.attrs->>'SpeedLimit','')::float8)",
    "speed_over_limit": "COALESCE(p.speed_over_limit, (p.speed - p.speed_limit))",
    "local_hour": f"EXTRACT(HOUR FROM (p.ts AT TIME ZONE '{CRASH_TIMEZONE}'))",
    "local_time": f"(p.ts AT TIME ZONE '{CRASH_TIMEZONE}')::time",
    "acc_x": "p.acc_x",
    "acc_y": "p.acc_y",
    "AccX": "p.acc_x",
    "AccY": "p.acc_y",
    "hp_acc_image_no": "NULLIF(p.attrs->>'hp_acc_image_no','')",
    "accident_id": "NULLIF(p.attrs->>'accident_id','')",
    "primary_id": "NULLIF(p.attrs->>'primary_id','')",
    "vehicle_id": "NULLIF(p.attrs->>'VehicleID','')",
}

# Map user-facing column names -> SQL expressions (crash/events)
_CRASH_SQL_COL = {
    "lat": "e.lat",
    "latitude": "e.lat",
    "lon": "e.lon",
    "longitude": "e.lon",
    "ts": "e.ts",
    "time": "e.ts",
    "timestamp": "e.ts",
    "road_segment_id": "e.road_segment_id",
    "road": "COALESCE(r.name, NULLIF(e.props->>'road',''), NULLIF(e.props->>'road_name',''), NULLIF(e.props->>'roadName',''))",
    "road_name": "COALESCE(r.name, NULLIF(e.props->>'road',''), NULLIF(e.props->>'road_name',''), NULLIF(e.props->>'roadName',''))",
    "name": "COALESCE(r.name, NULLIF(e.props->>'road',''), NULLIF(e.props->>'road_name',''), NULLIF(e.props->>'roadName',''))",
    "severity": "NULLIF(e.props->>'severity','')",
    "hp_acc_image_no": "NULLIF(e.props->>'hp_acc_image_no','')",
    "accident_id": "NULLIF(e.props->>'accident_id','')",
    "primary_id": "COALESCE(NULLIF(e.props->>'hp_acc_image_no',''),NULLIF(e.props->>'accident_id',''),NULLIF(e.props->>'primary_id',''),NULLIF(e.props->>'crash_id',''),NULLIF(e.props->>'id',''))",
    "accident_date": (
        "COALESCE("
        "NULLIF(e.props->>'_event_date_norm',''), "
        "NULLIF(e.props->>'accident_date',''), "
        "NULLIF(e.props->>'event_date',''), "
        "NULLIF(e.props->>'crash_date',''), "
        f"TO_CHAR((e.ts AT TIME ZONE '{CRASH_TIMEZONE}')::date, 'YYYY-MM-DD')"
        ")"
    ),
    "accident_time": (
        "COALESCE("
        "NULLIF(e.props->>'_event_time_norm',''), "
        "NULLIF(e.props->>'accident_time',''), "
        "NULLIF(e.props->>'event_time',''), "
        "NULLIF(e.props->>'crash_time',''), "
        f"TO_CHAR((e.ts AT TIME ZONE '{CRASH_TIMEZONE}')::time, 'HH24:MI:SS')"
        ")"
    ),
    "crash_time": (
        "COALESCE("
        "NULLIF(e.props->>'crash_time',''), "
        "NULLIF(e.props->>'accident_time',''), "
        "NULLIF(e.props->>'event_time',''), "
        f"TO_CHAR((e.ts AT TIME ZONE '{CRASH_TIMEZONE}')::time, 'HH24:MI:SS')"
        ")"
    ),
    "city": "NULLIF(e.props->>'city_name','')",
    "city_name": "NULLIF(e.props->>'city_name','')",
    "county": "COALESCE(NULLIF(e.props->>'county',''), NULLIF(e.props->>'modot_county_nm',''))",
    "modot_county_nm": "COALESCE(NULLIF(e.props->>'county',''), NULLIF(e.props->>'modot_county_nm',''))",
    "routeid": "NULLIF(e.props->>'routeid','')",
    "route_id": "NULLIF(e.props->>'routeid','')",
    "measure": "NULLIF(e.props->>'measure','')::float8",
    "way_id": "NULLIF(e.props->>'way_id','')",
    "match_method": "NULLIF(e.props->>'match_method','')",
    "road_ref": "COALESCE(NULLIF(e.props->>'road_ref',''), NULLIF(e.props->>'road',''))",
    "modot_district_abbr": "NULLIF(e.props->>'modot_district_abbr','')",
    "modot_district_no": "NULLIF(e.props->>'modot_district_no','')",
    "no_of_vehicles": "NULLIF(e.props->>'no_of_vehicles','')",
    "number_of_vehicles": "NULLIF(e.props->>'no_of_vehicles','')",
    "number_killed": "NULLIF(e.props->>'number_killed','')::float8",
    "number_injured": "NULLIF(e.props->>'number_injured','')::float8",
    "no_disab_injury": "NULLIF(e.props->>'no_disab_injury','')::float8",
    "no_of_minor_injury": "NULLIF(e.props->>'no_of_minor_injury','')::float8",
    "accident_type": "NULLIF(e.props->>'accident_type','')",
    "two_veh_analysis": "NULLIF(e.props->>'two_veh_analysis','')",
    "light_condition": "NULLIF(e.props->>'light_condition','')",
    "weather_cond_1": "NULLIF(e.props->>'weather_cond_1','')",
    "weather_cond_2": "NULLIF(e.props->>'weather_cond_2','')",
    "road_surface": "NULLIF(e.props->>'road_surface','')",
    "road_condition_1": "NULLIF(e.props->>'road_condition_1','')",
    "road_condition_2": "NULLIF(e.props->>'road_condition_2','')",
    "at_loc_speed_limit": "NULLIF(e.props->>'at_loc_speed_limit','')::float8",
    "on_loc_spd_lmt": "NULLIF(e.props->>'on_loc_spd_lmt','')::float8",
    "urban_rural_class": "NULLIF(e.props->>'urban_rural_class','')",
    "func_class_name": "NULLIF(e.props->>'func_class_name','')",
    "intersection_type": "NULLIF(e.props->>'intersection_type','')",
    "direction": "NULLIF(e.props->>'direction','')",
    "event_date": (
        "COALESCE("
        "NULLIF(e.props->>'_event_date_norm',''), "
        "NULLIF(e.props->>'event_date',''), "
        "NULLIF(e.props->>'accident_date',''), "
        "NULLIF(e.props->>'crash_date',''), "
        f"TO_CHAR((e.ts AT TIME ZONE '{CRASH_TIMEZONE}')::date, 'YYYY-MM-DD')"
        ")"
    ),
    "event_time": (
        "COALESCE("
        "NULLIF(e.props->>'_event_time_norm',''), "
        "NULLIF(e.props->>'event_time',''), "
        "NULLIF(e.props->>'accident_time',''), "
        "NULLIF(e.props->>'crash_time',''), "
        f"TO_CHAR((e.ts AT TIME ZONE '{CRASH_TIMEZONE}')::time, 'HH24:MI:SS')"
        ")"
    ),
    "local_hour": f"EXTRACT(HOUR FROM (e.ts AT TIME ZONE '{CRASH_TIMEZONE}'))",
    "local_time": f"(e.ts AT TIME ZONE '{CRASH_TIMEZONE}')::time",
    "start_date": "NULLIF(e.props->>'start_date','')",
    "end_date": "NULLIF(e.props->>'end_date','')",
    "vehicle_impact": "NULLIF(e.props->>'vehicle_impact','')",
    "location_method": "NULLIF(e.props->>'location_method','')",
    "reduced_speed_limit_kph": "NULLIF(e.props->>'reduced_speed_limit_kph','')",
}

__all__ = [
    "CRASH_TIMEZONE",
    "DRIVABLE_HIGHWAY_TAGS",
    "NON_DRIVABLE_HINT_TERMS",
    "TRAFFIC_MAP_LIMIT_DEFAULT",
    "TRAFFIC_MAP_LIMIT_MAX",
    "TRAFFIC_MAP_LIMIT_NEAR_MAX",
    "TRAFFIC_NEAR_COMBINED_MAX",
    "TRAFFIC_NEAR_CRASH_OVERLAY_MAX",
    "TRAFFIC_NEAR_OVERLAY_QUERY_TIMEOUT_MS",
    "TRAFFIC_NEAR_WORKZONE_OVERLAY_MAX",
    "TRAFFIC_RESULT_LIMIT_WITH_MAP",
    "TRAFFIC_SQL_RESULT_TIMEOUT_MS",
    "TRAFFIC_MAP_QUERY_TIMEOUT_MS",
    "_CRASH_SQL_COL",
    "_HB_SQL_COL",
    "_SQL_COL",
]
