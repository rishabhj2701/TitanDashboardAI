"""Shared geo column name preferences."""

GEO_LATITUDE_COLUMNS = [
    "SnappedLatitude",
    "LATITUDE",       # Iowa crash CSV (all-caps)
    "latitude",
    "Latitude",
    "lat",
    "Lat",
    "landed_latitude",
]

GEO_LONGITUDE_COLUMNS = [
    "SnappedLongitude",
    "LONGITUDE",      # Iowa crash CSV (all-caps)
    "longitude",
    "Longitude",
    "lon",
    "Lon",
    "landed_longitude",
]

__all__ = ["GEO_LATITUDE_COLUMNS", "GEO_LONGITUDE_COLUMNS"]
