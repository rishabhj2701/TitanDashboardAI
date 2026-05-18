"""Backward-compatible alias for the PostGIS store module."""

import sys as _sys

from .storage.postgis import store as _store

_sys.modules[__name__] = _store
