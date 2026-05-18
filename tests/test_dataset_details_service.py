from backend.services.dataset_details import build_dataset_detail


class _Store:
    def __init__(self):
        self.refresh_calls = []
        self._get_count = 0

    def get_dataset(self, dataset_id: str):
        self._get_count += 1
        base = {
            "dataset_id": dataset_id,
            "name": "stl_crashes_july",
            "entity_type": "crash",
            "created_at": "2025-02-10T00:00:00Z",
            "stats": {
                "ingest": {
                    "rows_inserted": 25,
                    "mapping_fields": {
                        "road_name": "road_name",
                    },
                    "lat_col": "latitude",
                    "lon_col": "longitude",
                },
                "road_match": {"matched": 20},
            },
            "codebook": {},
        }
        if self._get_count > 1:
            base["codebook"] = {"mappings": {"severity": {"1": "Fatal"}}}
        return base

    def list_codebook_attributes(self):
        return ["severity", "road_type"]

    def refresh_dataset_codebook_info(self, dataset_id: str, sample_rows: int = 3000):
        self.refresh_calls.append((dataset_id, sample_rows))

    def preview_events(self, dataset_id: str, limit: int = 6):
        return [
            {
                "ts": "2025-02-01T12:00:00Z",
                "lat": 38.6,
                "lon": -90.2,
                "props": {
                    "severity": "Fatal",
                    "road_name": "I-70",
                },
            }
        ]


def test_build_dataset_detail_enriches_dataset_and_flattens_preview_rows():
    store = _Store()

    detail = build_dataset_detail("crash_123", store=store)

    assert detail["row_count"] == 25
    assert detail["dataset"] == "stl_crashes_july"
    assert detail["ingested_at"] == "2025-02-10T00:00:00Z"
    assert detail["mapping"]["fields"]["road_name"] == "road_name"
    assert detail["road_match"]["road_segment_id_column"] == "road_segment_id"
    assert detail["road_match"]["road_column"] == "road_name"
    assert detail["geo"] == {"lat_column": "latitude", "lon_column": "longitude"}
    assert detail["codebook"]["available"] is True
    assert detail["codebook"]["attributes"] == 2
    assert detail["codebook_attributes"] == ["road_type", "severity"]
    assert detail["preview_rows"][0]["severity"] == "Fatal"
    assert "props" not in detail["preview_rows"][0]
    assert "severity" in detail["columns"]
    assert store.refresh_calls == [("crash_123", 3000)]


class _AttrsFailStore(_Store):
    def list_codebook_attributes(self):
        raise RuntimeError("no attributes")


def test_build_dataset_detail_handles_codebook_attribute_failures():
    store = _AttrsFailStore()
    detail = build_dataset_detail("crash_456", store=store)

    assert detail["codebook_attributes"] == []
    assert detail["codebook"]["available"] is False
    assert detail["codebook"]["attributes"] == 0

