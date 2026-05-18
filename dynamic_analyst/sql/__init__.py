"""Domain SQL operations package (lazy exports)."""

from __future__ import annotations


def run_crash_sql_operations(dataset, query):
    from .crash import run_crash_sql_operations as _impl

    return _impl(dataset, query)


def run_traffic_sql_operations(dataset, query):
    from .traffic import run_traffic_sql_operations as _impl

    return _impl(dataset, query)


def run_workzone_sql_operations(dataset, query):
    from .workzone import run_workzone_sql_operations as _impl

    return _impl(dataset, query)


def run_multi_dataset_conflation(
    dataset_names,
    max_distance_meters: float = 500.0,
    time_mode: str = "auto",
    time_window_minutes: int = 60,
    limit: int = 5000,
):
    from .conflation_ops import run_multi_dataset_conflation as _impl

    return _impl(
        dataset_names=dataset_names,
        max_distance_meters=max_distance_meters,
        time_mode=time_mode,
        time_window_minutes=time_window_minutes,
        limit=limit,
    )


def run_generic_conflation(
    left_dataset: str,
    right_dataset: str,
    max_distance_meters: float = 500.0,
    time_mode: str = "auto",
    time_window_minutes: int = 60,
    generate_map: bool = True,
    limit: int = 5000,
):
    from .conflation_ops import run_generic_conflation as _impl

    return _impl(
        left_dataset=left_dataset,
        right_dataset=right_dataset,
        max_distance_meters=max_distance_meters,
        time_mode=time_mode,
        time_window_minutes=time_window_minutes,
        generate_map=generate_map,
        limit=limit,
    )


def run_sql_domain_operations(domain: str, query: dict):
    from ..orchestration import run_sql_domain_operations as _impl

    return _impl(domain, query)


def run_unified_sql_query(plan: dict):
    from ..orchestration import run_unified_sql_query as _impl

    return _impl(plan)


def normalize_sql_domain(domain: str) -> str:
    from ..orchestration import normalize_sql_domain as _impl

    return _impl(domain)


def supported_sql_domains() -> list[str]:
    from ..orchestration import supported_sql_domains as _impl

    return _impl()


__all__ = [
    "run_crash_sql_operations",
    "run_traffic_sql_operations",
    "run_workzone_sql_operations",
    "run_multi_dataset_conflation",
    "run_generic_conflation",
    "run_sql_domain_operations",
    "run_unified_sql_query",
    "normalize_sql_domain",
    "supported_sql_domains",
]
