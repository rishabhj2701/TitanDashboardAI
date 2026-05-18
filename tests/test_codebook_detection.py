"""Regression tests for codebook vs event-data classification.

These run offline (no server required) — they test the detection
heuristics directly against synthetic DataFrames.
"""

import pandas as pd
import pytest
import sys, os

# Allow importing from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dynamic_analyst.postgis_store import (
    detect_codebook_schema,
    _looks_like_event_dataset,
)


# ---------------------------------------------------------------------------
# Fixtures: synthetic DataFrames
# ---------------------------------------------------------------------------

@pytest.fixture()
def crash_csv_narrow():
    """Mimics stl_crashes_july.csv — 6 columns, clearly crash data."""
    return pd.DataFrame({
        "hp_acc_image_no": [f"100{i:07d}" for i in range(20)],
        "accident_date": pd.date_range("2024-07-01", periods=20).astype(str),
        "accident_time": [f"{h:02d}:30:00" for h in range(20)],
        "severity": ["PROPERTY DAMAGE ONLY"] * 10 + ["MINOR INJURY"] * 10,
        "landed_latitude": [38.6 + i * 0.001 for i in range(20)],
        "landed_longitude": [-90.2 - i * 0.001 for i in range(20)],
    })


@pytest.fixture()
def real_codebook():
    """Mimics a MoDOT code-value lookup table with exact column names."""
    return pd.DataFrame({
        "hp_cd_val_attr": ["SEVERITY"] * 4 + ["ROAD_TYPE"] * 3,
        "hp_cd_val_code": ["1", "2", "3", "4", "A", "B", "C"],
        "description": [
            "Fatal", "Serious Injury", "Minor Injury", "Property Damage Only",
            "Interstate", "US Highway", "State Route",
        ],
    })


@pytest.fixture()
def generic_codebook():
    """Codebook without MoDOT column names — uses 'attribute'/'code'/'label'."""
    return pd.DataFrame({
        "attribute": ["COLOR"] * 5,
        "code": ["R", "G", "B", "Y", "W"],
        "label": ["Red", "Green", "Blue", "Yellow", "White"],
    })


@pytest.fixture()
def sales_csv():
    """Generic sales data — should NOT be classified as codebook."""
    return pd.DataFrame({
        "order_id": [f"ORD-{i}" for i in range(30)],
        "product": ["Widget A", "Widget B", "Gadget C"] * 10,
        "category": ["Electronics", "Home", "Office"] * 10,
        "amount": [19.99 + i for i in range(30)],
        "quantity": [1, 2, 3] * 10,
        "date": pd.date_range("2024-01-01", periods=30).astype(str),
        "region": ["North", "South", "East"] * 10,
        "lat": [38.6 + i * 0.01 for i in range(30)],
        "lon": [-90.2 - i * 0.01 for i in range(30)],
    })


@pytest.fixture()
def iot_csv():
    """IoT sensor readings — should NOT be classified as codebook."""
    return pd.DataFrame({
        "sensor_id": [f"S-{i % 5}" for i in range(30)],
        "temperature": [20.0 + i * 0.5 for i in range(30)],
        "humidity": [40.0 + i * 0.3 for i in range(30)],
        "pressure": [1013.0 + i * 0.1 for i in range(30)],
        "timestamp": pd.date_range("2024-06-01", periods=30, freq="h").astype(str),
        "lat": [38.6 + i * 0.001 for i in range(30)],
        "lon": [-90.2 - i * 0.001 for i in range(30)],
        "location_name": ["Site A", "Site B", "Site C", "Site D", "Site E"] * 6,
    })


@pytest.fixture()
def wide_event_csv():
    """Wide dataset with 30+ columns — should skip heuristic entirely."""
    data = {f"col_{i}": range(10) for i in range(30)}
    data["latitude"] = [38.6 + i * 0.001 for i in range(10)]
    data["longitude"] = [-90.2 - i * 0.001 for i in range(10)]
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Tests: _looks_like_event_dataset
# ---------------------------------------------------------------------------

class TestLooksLikeEventDataset:
    def test_crash_csv_narrow_detected_as_event(self, crash_csv_narrow):
        """Regression: stl_crashes_july.csv must be detected as event data."""
        assert _looks_like_event_dataset(crash_csv_narrow) is True

    def test_real_codebook_not_event(self, real_codebook):
        assert _looks_like_event_dataset(real_codebook) is False

    def test_generic_codebook_not_event(self, generic_codebook):
        assert _looks_like_event_dataset(generic_codebook) is False

    def test_sales_csv_detected_as_event(self, sales_csv):
        """Sales data has lat/lon + date — should look event-like."""
        assert _looks_like_event_dataset(sales_csv) is True

    def test_iot_csv_detected_as_event(self, iot_csv):
        """IoT data has lat/lon + timestamp — should look event-like."""
        assert _looks_like_event_dataset(iot_csv) is True


# ---------------------------------------------------------------------------
# Tests: detect_codebook_schema (full pipeline)
# ---------------------------------------------------------------------------

class TestDetectCodebookSchema:
    def test_crash_csv_not_codebook(self, crash_csv_narrow):
        """Regression: stl_crashes_july.csv must NOT be classified as codebook."""
        result = detect_codebook_schema(crash_csv_narrow)
        assert result is None

    def test_real_codebook_detected(self, real_codebook):
        """MoDOT codebook with exact column names should be detected."""
        result = detect_codebook_schema(real_codebook)
        assert result is not None
        assert result["attr"] == "hp_cd_val_attr"
        assert result["code"] == "hp_cd_val_code"
        assert result["label"] == "description"

    def test_generic_codebook_detected(self, generic_codebook):
        """Codebook with attribute/code/label columns should be detected."""
        result = detect_codebook_schema(generic_codebook)
        assert result is not None
        assert result["attr"] == "attribute"
        assert result["code"] == "code"
        assert result["label"] == "label"

    def test_sales_csv_not_codebook(self, sales_csv):
        result = detect_codebook_schema(sales_csv)
        assert result is None

    def test_iot_csv_not_codebook(self, iot_csv):
        result = detect_codebook_schema(iot_csv)
        assert result is None

    def test_wide_dataset_not_codebook(self, wide_event_csv):
        result = detect_codebook_schema(wide_event_csv)
        assert result is None

    def test_empty_df_not_codebook(self):
        result = detect_codebook_schema(pd.DataFrame())
        assert result is None

    def test_none_input(self):
        result = detect_codebook_schema(None)
        assert result is None
