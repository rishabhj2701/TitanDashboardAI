"""Shared geo column name preferences."""

GEO_LATITUDE_COLUMNS = [
    "SnappedLatitude",
    "latitude",
    "Latitude",
    "lat",
    "Lat",
    "landed_latitude",
]

GEO_LONGITUDE_COLUMNS = [
    "SnappedLongitude",
    "longitude",
    "Longitude",
    "lon",
    "Lon",
    "landed_longitude",
]

__all__ = ["GEO_LATITUDE_COLUMNS", "GEO_LONGITUDE_COLUMNS"]
