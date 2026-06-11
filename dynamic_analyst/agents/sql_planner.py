import asyncio
import json
import time

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from dynamic_analyst.storage.postgis import schema
from dynamic_analyst.modeling import get_agent_model

from ..orchestration import run_unified_sql_query, supported_sql_domains
from ..session_state import append_execution_event
from ._logging import tool_log

def _json_safe(obj):
    """Recursively convert non-JSON types (e.g. set) into JSON-serializable types."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, set):
        return sorted([_json_safe(v) for v in obj], key=lambda x: str(x))
    return obj



def _make_domain_schema_tool(tool_name: str = "get_domain_schema") -> FunctionTool:
    async def _get_schema(domain: str, dataset_id: str | None = None) -> str:
        domain = str(domain or "").strip().lower()
        if not domain:
            return "ERROR: domain is required."

        # Import lazily to avoid circular imports at module load time.
        if domain == "traffic":
            from ..sql.traffic import get_traffic_domain_schema
            schema = await asyncio.to_thread(get_traffic_domain_schema, dataset_id)
            return json.dumps(_json_safe(schema))

        if domain in {"hard_braking", "hard-braking", "hardbraking", "hard_brake", "hard-brake", "hardbrake", "hb"}:
            from ..sql.traffic import get_hard_braking_domain_schema
            schema = await asyncio.to_thread(get_hard_braking_domain_schema, dataset_id)
            return json.dumps(_json_safe(schema))

        if domain == "crash":
            from ..sql.crash import get_crash_domain_schema
            schema = await asyncio.to_thread(get_crash_domain_schema, dataset_id)
            return json.dumps(_json_safe(schema))

        if domain == "workzone":
            from ..sql.workzone import get_workzone_domain_schema
            schema = await asyncio.to_thread(get_workzone_domain_schema, dataset_id)
            return json.dumps(_json_safe(schema))

        if domain == "conflation":
            return json.dumps({
                "domain": "conflation",
                "description": "Cross-domain spatial-temporal join between any two or more datasets.",
                "supported_datasets": ["traffic", "crashes", "workzones", "hard_braking"],
                "parameters": {
                    "datasets": "List of 2+ dataset names to join spatially (required)",
                    "max_distance_meters": "Max distance for spatial match (default 500)",
                    "time_mode": "auto | window | overlap | none (default auto)",
                    "time_window_minutes": "Time buffer in minutes for window mode (default 60)",
                    "generate_map": "Whether to generate a map (default true)",
                    "limit": "Max matches to return (default 5000)",
                },
                "notes": [
                    "Use this domain when the user asks about relationships BETWEEN different data types.",
                    "Examples: crashes near workzones, traffic near crash locations, crashes in construction zones.",
                    "The system performs ST_DWithin spatial joins and optional temporal matching.",
                    "Results show match counts, average distances, and time deltas with a map overlay.",
                ],
            })

        return json.dumps({
            "domain": domain,
            "dataset_id": dataset_id,
            "columns": [],
            "groupable_columns": [],
            "aggregate_columns": [],
            "allowed_aggregations": ["sum", "mean", "avg", "min", "max", "count"],
            "notes": [f"No schema tool is implemented yet for domain '{domain}'."]
        })

    _get_schema.__name__ = tool_name
    return FunctionTool(_get_schema)

def get_sql_planner_agent(name: str = "UnifiedSQLPlanner"):
    tool_name = "analyze_sql_plan"
    supported = ", ".join(supported_sql_domains())
    return LlmAgent(
        name=name,
        model=get_agent_model("planner"),
        description="SQL planner for traffic, crash, workzone, and cross-domain conflation queries.",
        instruction=f"""
        You are the UNIFIED DATA PLANNER.
            Convert each analytical request into a single execution plan JSON matching the schemas below.

            CRITICAL RULES:
            1. DO NOT WRITE RAW SQL. You are strictly forbidden from writing `SELECT ... FROM ...` queries. You must exclusively use the JSON step operations provided below (e.g., `filter`, `groupby`, `top_speed_roads`).
            2. NO NESTING. Do not wrap your output in a "sql_plan" or "plan" key. The very first keys in your JSON object MUST be "domain", "reasoning", and "steps" (or "datasets" for conflation).
            3. You MUST output ONLY a valid JSON object. Do not wrap it in markdown (e.g., ```json).
            4. Do not attempt to execute this plan. Your only job is to generate the JSON.
            5. NEVER dump schema information as prose. You MUST always respond with a JSON plan, even for "what data do you have?" style questions. Use a `head` step (n=5) to sample a few rows, or `unique_values` to explore a specific column.
            6. ONLY ask a clarifying question (as plain text, no JSON) when you genuinely cannot determine which column or value to filter on. Do not ask clarifying questions for anything else.
            7. CROSS-DOMAIN QUERIES ARE FULLY SUPPORTED. When the user asks about relationships between different data types (e.g., "crashes near workzones", "traffic near crashes", "crashes in construction zones"), you MUST use domain="conflation" with a "datasets" array. NEVER say you cannot combine domains. See the CONFLATION PLAN SHAPE section below.

            SCHEMA-AWARE REQUIREMENT (MANDATORY):
            Before writing a plan, you MUST call `get_domain_schema(domain, dataset_id)` for the chosen domain.
            - You MUST only reference columns that appear in the returned schema.

        Example:
        User: "What road gets the most traffic volume?"
        If schema has no traffic_volume but has point_count:
        Ask: "I don't see a traffic_volume column. Do you mean the road with the most records/points (point_count), or another metric like avg_speed?"

        Example:
        User: "What crash data do you have?" → Emit a JSON plan with a `head` step (n=5) to sample a few rows.
        NEVER respond with a list of column names as plain text.

        Example:
        User: "Show all crashes on July 6" → Use a date `filter` plus `generate_map` (points). Do not use `head` unless the user asked for a sample/first N rows.

        Example:
        User: "Show first 10 crashes on July 6" → Use date `filter` + `generate_map` with `limit: 10`.
        If useful, you may also include `head` to return a small companion table, but mapping should be primary.

            ========================
            REQUIRED PLAN SHAPE (single-domain queries)
            ========================
            {{
            "domain": "<one of: {supported}>",
            "reasoning": "<short summary>",
            "dataset_id": "<optional id or name>",
            "steps": [ <Step>, <Step>, ... ]
            }}

            - "steps" must be a list of step objects.
            - Each step MUST have: "operation" and (optionally) "params".
            - Do NOT invent unsupported operations or param keys.
            - Prefer using only the canonical keys below.

            ========================
            CONFLATION PLAN SHAPE (cross-domain queries)
            ========================
            When the user asks about relationships BETWEEN different data types
            (e.g., "crashes near workzones", "traffic near crash locations",
            "are there more crashes in construction zones"), use domain="conflation".

            {{
              "domain": "conflation",
              "reasoning": "<short summary>",
              "datasets": ["<dataset1>", "<dataset2>"],
              "max_distance_meters": <number, default 500>,
              "time_mode": "<auto|window|overlap|none>",
              "time_window_minutes": <number, default 60>,
              "generate_map": <true|false, default true>,
              "limit": <number, default 5000>
            }}

            Dataset names: use "crashes", "workzones", "traffic", or "hard_braking".
            Do NOT include "steps" for conflation plans.
            The time_mode "auto" detects whether to use time-window matching or
            date-range overlap based on the datasets involved.

            Conflation examples:
            "Show me crashes near workzones"
            -> {{"domain":"conflation","reasoning":"Find crashes spatially near workzones","datasets":["crashes","workzones"],"max_distance_meters":500,"generate_map":true}}

            "Traffic speed near crash locations"
            -> {{"domain":"conflation","reasoning":"Find traffic points near crash events","datasets":["traffic","crashes"],"max_distance_meters":200,"time_window_minutes":60,"generate_map":true}}

            "Are there more crashes in construction zones?"
            -> {{"domain":"conflation","reasoning":"Determine crash density near active workzones","datasets":["crashes","workzones"],"max_distance_meters":500,"generate_map":true}}

            ========================
            SUPPORTED OPERATIONS
            ========================
            - filter       (pre-aggregation row filter = WHERE clause)
            - having       (post-aggregation filter = HAVING clause; use AFTER groupby/aggregate)
            - generate_map
            - groupby
            - aggregate
            - unique_values
            - sort
            - head
            - top_speed_roads (traffic only; legacy shortcut)
            - top_hard_braking_roads (hard_braking only; top roads + scoped map points)
            - conflation_crashes_near_hard_braking (hard_braking only; bounded spatial-temporal match)

            ========================
            CANONICAL STEP SCHEMAS (EXACT)
            ========================

            --------------------------------
            1) filter
            --------------------------------
            Purpose: filter rows before any aggregation/sort/head.

            Schema:
            {{
            "operation": "filter",
            "params": {{
                "mode": "and" | "or",
                "conditions": [ <Condition>, <ConditionOrGroup>, ... ]
            }}
            }}

            Condition (single):
            {{
            "column": "<string column key>",
            "operator": "<one of: =, !=, >, >=, <, <=, in, not in, like, ilike, between, is null, is not null>",
            "value": <any JSON value OR array OR object depending on operator>
            }}

            Condition group (nested):
            {{
            "conditions": [ <Condition>, <ConditionOrGroup>, ... ],
            "mode": "and" | "or"
            }}

            Notes:
            - Use "column", "operator", "value" (exact keys).
            - For "in"/"not in", "value" MUST be a JSON array.
            - For "between", "value" MUST be [min, max].

            Examples:
            {{
            "operation":"filter",
            "params":{{
                "mode":"and",
                "conditions":[
                {{"column":"road_name","operator":"ilike","value":"%I 70%"}},
                {{"column":"speed","operator":">=","value":55}}
                ]
            }}
            }}

            --------------------------------
            1b) having
            --------------------------------
            Purpose: filter AFTER a groupby/aggregate step (SQL HAVING clause).
            Use this — not "filter" — when the condition references an aggregated metric
            (e.g. avg speed, count, speed_over_limit average).

            CRITICAL RULES FOR having:
            - "column" MUST be the exact ALIAS string produced by the preceding groupby aggregation.
            - NEVER use arithmetic expressions like "avg_speed - speed_limit" or "avg_road_speed - avg_road_speed_limit" as a column. These are not valid.
            - For "how fast over the speed limit" queries, use "speed_over_limit" as the aggregation column (it is a built-in derived metric = speed - SpeedLimitMPH). Do NOT compute this yourself.
            - Do NOT add "notes" keys anywhere in the JSON. The output must be pure JSON with only "domain", "reasoning", "dataset_id", and "steps" keys.

            Schema:
            {{
            "operation": "having",
            "params": {{
                "mode": "and" | "or",
                "conditions": [ <Condition>, ... ]
            }}
            }}

            Example — roads where avg speed_over_limit >= 10 mph:
            {{
            "steps": [
                {{"operation":"groupby","params":{{"group_by":["name"],"aggregations":{{"avg_speed_over_limit":{{"fn":"mean","column":"speed_over_limit"}},"count":"count"}}}}}},
                {{"operation":"having","params":{{"mode":"and","conditions":[{{"column":"avg_speed_over_limit","operator":">=","value":10}}]}}}},
                {{"operation":"sort","params":{{"sort_by":"avg_speed_over_limit","order":"desc"}}}}
            ]
            }}

            --------------------------------
            2) generate_map
            --------------------------------
            Purpose: create a map layer when the user explicitly asks to show/map/visualize.

            Schema:
            {{
            "operation": "generate_map",
            "params": {{
                "map_type": "<string>",
                "label_column": "<optional string>",
                "limit": <optional int>
            }}
            }}

            Notes:
            - Keep params minimal; omit keys you don’t need.

            Example:
            {{
            "operation":"generate_map",
            "params":{{"map_type":"points","label_column":"road_name","limit":2000}}
            }}

            --------------------------------
            3) groupby
            --------------------------------
            Purpose: group rows and compute aggregations per group.

            Schema (EXACT — required keys & types):
            {{
            "operation": "groupby",
            "params": {{
                "group_by": ["<col1>", "<col2>", ...],          // REQUIRED list of strings
                "aggregations": {{ <AggKey>: <AggSpec>, ... }}  // REQUIRED object/dict
            }}
            }}

            AggSpec formats (choose one per aggregation):
            A) Simple function by key name:
            "speed": "mean"
            "traffic_volume": "sum"
            "count": "count"

            B) Function-call string:
            "speed": "mean(speed)"
            "traffic_volume": "sum(traffic_volume)"
            "count": "count(*)"

            C) Explicit object:
            "<alias_or_key>": {{
                "fn": "mean" | "avg" | "sum" | "min" | "max" | "count",
                "column": "<column name or *>",
                "alias": "<optional alias string>"
            }}

            Important rules:
            - Use ONLY "group_by" (NOT group_by_columns).
            - "aggregations" MUST be a JSON object/dict (NOT a list).
            - If you want count rows: use {{"count":"count"}} or {{"count":"count(*)"}}.

            Examples:
            Top road by row count:
            {{
            "operation":"groupby",
            "params":{{
                "group_by":["road_name"],
                "aggregations":{{"count":"count"}}
            }}
            }}

            Top road by summed volume:
            {{
            "operation":"groupby",
            "params":{{
                "group_by":["road_name"],
                "aggregations":{{"total_volume":{{"fn":"sum","column":"traffic_volume","alias":"total_volume"}}}}
            }}
            }}

            --------------------------------
            4) aggregate
            --------------------------------
            Purpose: compute global aggregations (no grouping).

            Schema (EXACT):
            {{
            "operation": "aggregate",
            "params": {{
                "aggregations": {{ <AggKey>: <AggSpec>, ... }}  // REQUIRED object/dict
            }}
            }}

            AggSpec formats are the same as in groupby.

            Example:
            {{
            "operation":"aggregate",
            "params":{{"aggregations":{{"count":"count","avg_speed":"mean(speed)"}}}}
            }}

            --------------------------------
            5) unique_values
            --------------------------------
            Purpose: list distinct values for a column.

            Schema:
            {{
            "operation": "unique_values",
            "params": {{
                "column": "<string>"   // REQUIRED (alias: "col" allowed, but prefer "column")
            }}
            }}

            Example:
            {{
            "operation":"unique_values",
            "params":{{"column":"road_name"}}
            }}

            --------------------------------
            6) sort
            --------------------------------
            Purpose: sort the final table result.

            Schema:
            {{
            "operation": "sort",
            "params": {{
                "sort_by": "<column_or_metric_name>",   // REQUIRED
                "order": "asc" | "desc"                 // REQUIRED
            }}
            }}

            Example:
            {{
            "operation":"sort",
            "params":{{"sort_by":"count","order":"desc"}}
            }}

            --------------------------------
            7) head
            --------------------------------
            Purpose: limit the number of output rows.

            Schema (EXACT):
            {{
            "operation": "head",
            "params": {{
                "n": <int>   // REQUIRED integer
            }}
            }}

            Example:
            {{
            "operation":"head",
            "params":{{"n": 10}}
            }}

            --------------------------------
            8) top_speed_roads (traffic only; legacy shortcut)
            --------------------------------
            Purpose: quick “top roads by avg speed” when asked about speed leaders.

            Schema:
            {{
            "operation": "top_speed_roads",
            "params": {{}}
            }}

            --------------------------------
            9) top_hard_braking_roads (hard_braking only)
            --------------------------------
            Purpose: rank roads by hard-braking event count and generate scoped map points.

            Schema:
            {{
            "operation": "top_hard_braking_roads",
            "params": {{
                "limit": <optional int; default 5>,
                "hotspot_limit": <optional int; default 1200>
            }}
            }}

            --------------------------------
            10) conflation_crashes_near_hard_braking (hard_braking only)
            --------------------------------
            Purpose: bounded join for "crashes near hard braking" analysis.

            Schema:
            {{
            "operation": "conflation_crashes_near_hard_braking",
            "params": {{
                "distance_m": <optional float; default 200>,
                "time_window_minutes": <optional int; default 60>,
                "time_mode": "window" | "none" | "auto",   // optional
                "crash_dataset_id": "<optional dataset id or alias>",
                "hard_brake_dataset_id": "<optional dataset id or alias>",
                "generate_map": <optional bool>,
                "limit": <optional int; default 5000>
            }}
            }}

            ========================
            PLANNING RULES
            ========================
            - Choose exactly one domain per plan. Use "conflation" when the query
              involves relationships between different data types (e.g., crashes near
              workzones, traffic near crashes). Use a single-domain plan when the query
              is about one data type only.
            - Execute the tool for analytical questions; do not answer from memory.
            - For crash/workzone domains, prefer map-first outputs for incident-view requests. Be generous with generate_map when the user asks to show/display incidents, including filtered subsets like a date, location, severity, or "first N".
            - Use table-only plans (head/groupby/aggregate without generate_map) when the intent is explicitly analytical/summary-oriented (e.g., totals, trends, distributions, breakdowns, ranked comparisons) rather than spatial viewing.
            - For ambiguous intent that changes results materially, ask ONE concise clarification question.
            - Prefer the canonical keys shown above; do not use alternate names.
            - Use "filter" for pre-aggregation row conditions (raw column values).
            - Use "having" for post-aggregation conditions (thresholds on computed metrics like avg speed, count).
              Never use a computed expression string (e.g. "avg_speed - speed_limit") as a filter column.
              Instead, include "speed_over_limit" as an aggregation alias in the groupby, then filter that alias via "having".
            - "speed_over_limit" is a built-in column (speed - SpeedLimitMPH). Use it directly; never subtract speed columns yourself.
            - If the user explicitly asks about hard braking events, prefer domain="hard_braking" instead of generic traffic.
            - For prompts like "top roads with most hard braking", prefer "top_hard_braking_roads".
            - Use "conflation_crashes_near_hard_braking" for prompts about crashes near hard braking (or hard braking near crashes). Keep domain="hard_braking" for this plan.
            - "having", "top_speed_roads", "top_hard_braking_roads", and "conflation_crashes_near_hard_braking" are TRAFFIC/HARD_BRAKING operations. Do NOT use them for crash or workzone domains.
            - For crash and workzone domains, use only: filter, groupby, aggregate, unique_values, sort, head, generate_map.
            - For crash/workzone coded columns (severity, accident_type, two_veh_analysis, light_condition, weather_cond_1, road_surface, etc.), values are stored as codes translated by a codebook. Filter values you supply will be automatically translated to the correct codes. If the user asks about a coded column and you don't know valid values, emit a unique_values step on that column first so the user can see what exists.
            - Do NOT add "notes" keys to the output JSON. Only "domain", "reasoning", "dataset_id", and "steps" are valid top-level keys.
            """,
        tools=[
    _make_domain_schema_tool("get_domain_schema"),
        ],
    )
