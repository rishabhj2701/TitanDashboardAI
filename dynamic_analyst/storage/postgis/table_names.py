from __future__ import annotations

import os
import re


_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _schema_name() -> str:
    raw = (os.environ.get("APP_DATA_SCHEMA") or "app_data").strip()
    if not _SAFE_IDENT_RE.match(raw):
        raise ValueError(f"Invalid APP_DATA_SCHEMA '{raw}'")
    return raw


APP_SCHEMA = _schema_name()

APP_DATASETS = f"{APP_SCHEMA}.datasets"
APP_EVENTS = f"{APP_SCHEMA}.events"
APP_DATASET_CODEBOOK = f"{APP_SCHEMA}.dataset_codebook"
APP_USER_CV_RUN_CONFIG = f"{APP_SCHEMA}.user_cv_run_config"
