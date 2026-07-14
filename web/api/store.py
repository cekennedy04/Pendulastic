"""
store.py
========
Shared in-memory data stores for the API layer.

In production, replace these dicts with SQLite (via aiosqlite) or PostgreSQL.
The module is imported by routers so they share the same object reference.
"""
from __future__ import annotations

from typing import Dict

# Keyed by trial_id (8-char UUID prefix)
trial_db: Dict[str, dict] = {}

# Keyed by participant_id
participant_db: Dict[str, dict] = {}
