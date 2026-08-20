"""SQLite-backed cache for dimension lookup results.

Cache key: normalised URL.
Resolved results are never re-fetched.
Needs Review / Not Found results are only re-fetched when force_retry=True.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from adapters.base import DimensionResult

_DB_PATH = Path(__file__).parent.parent / 'cache' / 'dimension_cache.db'


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dim_cache (
            url          TEXT PRIMARY KEY,
            confidence   TEXT,
            height       TEXT,
            width        TEXT,
            depth        TEXT,
            source_url   TEXT,
            raw_text     TEXT,
            reason       TEXT,
            fetched_at   TEXT
        )
    """)
    # Schema migration: add image columns to existing databases
    existing_cols = {row[1] for row in conn.execute('PRAGMA table_info(dim_cache)')}
    for col_def in ('image_bytes BLOB', 'image_status TEXT'):
        if col_def.split()[0] not in existing_cols:
            conn.execute(f'ALTER TABLE dim_cache ADD COLUMN {col_def}')
    conn.commit()
    return conn


def _normalise_url(url: str) -> str:
    return url.strip().rstrip('/')


def get_cached(url: str) -> Optional[DimensionResult]:
    """Return the cached DimensionResult for url, or None if not cached."""
    key = _normalise_url(url)
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT confidence, height, width, depth, source_url, raw_text, reason, "
            "image_bytes, image_status "
            "FROM dim_cache WHERE url = ?",
            (key,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return DimensionResult(
        confidence=row[0],
        height=row[1]     or None,
        width=row[2]      or None,
        depth=row[3]      or None,
        source_url=row[4] or '',
        raw_text=row[5]   or '',
        reason=row[6]     or '',
        image_bytes=row[7],
        image_status=row[8] or 'Not Found',
    )


def store_result(url: str, result: DimensionResult) -> None:
    """Persist a DimensionResult to the cache."""
    key = _normalise_url(url)
    conn = _connect()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO dim_cache
              (url, confidence, height, width, depth, source_url, raw_text, reason, fetched_at,
               image_bytes, image_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            key,
            result.confidence,
            result.height,
            result.width,
            result.depth,
            result.source_url,
            result.raw_text,
            result.reason,
            datetime.now(timezone.utc).isoformat(),
            result.image_bytes,
            result.image_status,
        ))
        conn.commit()
    finally:
        conn.close()


def should_fetch(url: str, force_retry: bool) -> bool:
    """
    Return True if the URL needs a fresh fetch.

    Rules:
      - Not in cache                         → always fetch
      - Cached as 'Resolved'                 → never re-fetch
      - Cached as anything else + force_retry → re-fetch
      - Cached as anything else + no retry   → use cached result
    """
    cached = get_cached(url)
    if cached is None:
        return True
    if cached.confidence == 'Resolved':
        return False
    return force_retry
