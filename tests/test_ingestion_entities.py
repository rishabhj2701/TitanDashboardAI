from backend.services.ingestion_entities import compute_mapping_status


def test_compute_mapping_status_accepts_composite_timestamp_source():
    status = compute_mapping_status(
        "crash",
        {
            "event_date": "accident_date",
            "event_time": "accident_time",
            "timestamp": "accident_date+accident_time",
            "latitude": "lat",
            "longitude": "lon",
        },
        available_columns=["accident_date", "accident_time", "lat", "lon"],
    )

    assert status["level"] == "ready"
    stale_warning = "timestamp -> accident_date+accident_time"
    assert all(stale_warning not in warning for warning in (status.get("warnings") or []))
