# Sample data

Three small datasets you can upload through the web UI to exercise the app end-to-end without needing real-scale CV or OSM data. All coordinates fall in the St. Louis, Missouri metro area.

| File | Format | What it covers |
| --- | --- | --- |
| `crashes_sample.csv` | CSV (MoDOT-style columns) | Crash uploads. 5 rows on I-44 / I-64 / I-70 / MO-370 / MO-141. |
| `cv_sample.csv` | CSV | Connected-vehicle upload smoke test. 20 rows across 2 vehicles, includes one hard-braking event (`accx <= -0.3`) on Forest Park Pkwy. |
| `workzones_sample.json` | WZDx FeatureCollection | Workzone upload. 3 work zones on I-44, I-70, and Forest Park Pkwy. |

## Running with these alone

You can register, upload all three through the web UI, ask the agent questions, and see results on the map without any pipeline pieces — just `docker compose --profile localdb up --build` plus the bootstrap SQL in `db/init/`. Uploads land in `app_data.events` directly.

The `way_id` / `road_dist_m` / `road_segment_id` columns on each event will be `NULL` because there's no road network to match against. Everything else works: time filters, severity histograms, point/line maps, agent chat over the uploaded data.

## Running with these plus an OSM road network

If you want road match (`way_id` populated on each upload), road tile rendering, and road-level aggregates, you need an OSM road network for Missouri. The **OSM road network** section of the main `README.md` walks through:

1. Downloading `north-america/us/missouri-latest.osm.pbf` from Geofabrik.
2. Importing it with `osm2pgsql` into your PostGIS DB.
3. Running `scripts/cv_conflation_pipeline.py` with `REFRESH_OSM_MATCH=1` to derive `public.osm_roads_match` from the imported `planet_osm_line`.

Once `public.osm_roads_match` is populated, each future crash / CV / workzone upload will get matched to nearby roads on ingest.

You can also run the conflation pipeline against `cv_sample.csv` itself if you gzip it first:

```bash
gzip -k tests/sample_data/cv_sample.csv
# Then point CV_CSV_GZ at the .gz path and run scripts/cv_conflation_pipeline.py
```

The match rate will be modest because the sample is only 20 points — that's expected. The pipeline is designed for millions of rows, not dozens.

## Notes

These files are fabricated for demo and smoke-testing purposes — they're not real MoDOT data. Coordinates are positioned to fall on real Missouri roads if you import the Missouri OSM extract. If you import OSM via the steps in the main README, the road geometry comes from OpenStreetMap (© OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright)).

The other CSVs in this directory (`sales_50.csv`, `iot_sensors_50.csv`, `survey_responses_50.csv`) are deliberately non-traffic fixtures used by `tests/test_language_bleed.py` and `tests/test_eval.py` (both opt-in via `RUN_EVAL=1`) to verify the agent handles cross-domain datasets without bleeding traffic terminology. Ignore them for the smoke-test path above.
