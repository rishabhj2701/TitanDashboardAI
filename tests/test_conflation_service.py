import pandas as pd

from dynamic_analyst.services.conflation_service import build_conflation_response


def test_build_conflation_response_zero_matches_includes_numeric_counts():
    text = build_conflation_response(
        match_df=pd.DataFrame(columns=["left_id", "right_id", "distance_meters", "time_delta_minutes"]),
        left_info={"name": "Crashes", "entity_type": "crash"},
        right_info={"name": "Hard Braking", "entity_type": "hard_braking"},
        left_id="crashes",
        right_id="hard_braking",
        max_distance_meters=2000.0,
        time_mode="window",
        time_window_minutes=180,
        generate_map=False,
        map_point_count=0,
    )

    assert "Total matches: 0" in text
    assert "0 crash records matched" in text
    assert "0 hard_braking records matched" in text
    assert "+/-180 minutes" in text
