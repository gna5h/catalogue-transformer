"""SQLite-backed cache for dimension lookup results.

Cache key: normalised URL.
Resolved results are never re-fetched unless the adapter's dimension-parsing
logic has changed (dim_version mismatch).
Embedded image results are never re-fetched unless the adapter's image-selection
logic has changed (img_version mismatch).

Adapter version registry
------------------------
ADAPTER_VERSIONS maps brand name → {dim: int, img: int}.
Bump the relevant key in ADAPTER_VERSIONS whenever that adapter's
parsing/selection logic changes — this causes matching cached rows to be
treated as stale and re-fetched on the next run, without requiring a manual
force_retry or cache wipe.

Versioning is per-concern (dim vs. img), not per adapter file:
  - A dimension-logic fix only invalidates cached dimension results.
  - An image-selection fix only invalidates cached image results.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from adapters.base import DimensionResult

_DB_PATH = Path(__file__).parent.parent / 'cache' / 'dimension_cache.db'

# ---------------------------------------------------------------------------
# Adapter version registry
# ---------------------------------------------------------------------------

ADAPTER_VERSIONS: dict[str, dict[str, int]] = {
    'Fisher & Paykel': {'dim': 1, 'img': 1},
    'Haier':           {'dim': 1, 'img': 1},
    'Bosch':           {'dim': 1, 'img': 2},  # img bumped: JSON-LD primary shot (was og:image)
    'LG':              {'dim': 1, 'img': 1},
    'Samsung':         {'dim': 3, 'img': 3},  # img bumped: gallery CDN extraction is now primary strategy
    'Westinghouse':    {'dim': 1, 'img': 1},
    'Electrolux':      {'dim': 1, 'img': 1},
    'Miele':           {'dim': 1, 'img': 1},
    'Smeg':            {'dim': 1, 'img': 1},
    'Beko':            {'dim': 1, 'img': 1},
}


def _current_versions(brand: str) -> tuple[int, int]:
    """Return (dim_version, img_version) for a brand, defaulting to (1, 1) if unknown."""
    vs = ADAPTER_VERSIONS.get(brand, {})
    return vs.get('dim', 1), vs.get('img', 1)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

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
    # Schema migrations — add columns to existing databases as needed
    existing_cols = {row[1] for row in conn.execute('PRAGMA table_info(dim_cache)')}
    for col_def in (
        'image_bytes BLOB',
        'image_status TEXT',
        'dim_version INTEGER',
        'img_version INTEGER',
    ):
        if col_def.split()[0] not in existing_cols:
            conn.execute(f'ALTER TABLE dim_cache ADD COLUMN {col_def}')
    conn.commit()
    return conn


def _normalise_url(url: str) -> str:
    return url.strip().rstrip('/')


def _get_cache_entry(url: str) -> Optional[tuple]:
    """Fetch the raw cache row or None.

    Columns (by index):
      0  confidence
      1  height
      2  width
      3  depth
      4  source_url
      5  raw_text
      6  reason
      7  image_bytes
      8  image_status
      9  dim_version
      10 img_version
    """
    key = _normalise_url(url)
    conn = _connect()
    try:
        return conn.execute(
            "SELECT confidence, height, width, depth, source_url, raw_text, reason, "
            "image_bytes, image_status, dim_version, img_version "
            "FROM dim_cache WHERE url = ?",
            (key,)
        ).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cached(url: str) -> Optional[DimensionResult]:
    """Return the cached DimensionResult for url, or None if not cached."""
    row = _get_cache_entry(url)
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


def store_result(url: str, result: DimensionResult, brand: str = '') -> None:
    """Persist a DimensionResult to the cache, tagged with the adapter's current versions."""
    key = _normalise_url(url)
    dim_v, img_v = _current_versions(brand)
    conn = _connect()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO dim_cache
              (url, confidence, height, width, depth, source_url, raw_text, reason, fetched_at,
               image_bytes, image_status, dim_version, img_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            dim_v,
            img_v,
        ))
        conn.commit()
    finally:
        conn.close()


def should_fetch_dims(url: str, force_retry: bool, brand: str = '') -> bool:
    """
    Return True if the URL needs a fresh dimension fetch.

    Rules (evaluated in order):
      - Not in cache                                                   → always fetch
      - In cache, brand known, dim_version differs                     → stale; fetch
      - In cache, confidence == 'Resolved', version current,
        force_retry=True                                               → forced re-fetch
      - In cache, confidence == 'Resolved', version current,
        force_retry=False                                              → use cache
      - In cache, non-Resolved                                         → always fetch
                                                                         (re-fetched each run until Resolved)

    NULL stored versions are treated as version 1 (pre-versioning legacy rows).
    """
    row = _get_cache_entry(url)
    if row is None:
        return True
    confidence = row[0]
    stored_dim_v = row[9] if row[9] is not None else 1
    if brand:
        current_dim_v, _ = _current_versions(brand)
        if stored_dim_v != current_dim_v:
            return True  # adapter dimension logic changed — treat as stale
    if confidence == 'Resolved':
        return force_retry  # honour caller; False=use cache, True=force re-fetch
    return True  # non-Resolved → always stale; retry every run


def should_fetch_image(url: str, brand: str = '') -> bool:
    """
    Return True if the URL needs a fresh image fetch.

    A cached 'Embedded' result is only accepted when the adapter's img_version
    matches — if it differs (adapter image-selection logic changed), the row is
    treated as stale and re-fetched regardless of Embedded status.
    NULL stored versions are treated as version 1.
    """
    row = _get_cache_entry(url)
    if row is None:
        return True
    img_status = row[8] or 'Not Found'
    stored_img_v = row[10] if row[10] is not None else 1
    if brand:
        _, current_img_v = _current_versions(brand)
        if stored_img_v != current_img_v:
            return True  # adapter image logic changed — treat as stale
    return img_status != 'Embedded'


# Backward-compatible alias used by external scripts
def should_fetch(url: str, force_retry: bool) -> bool:
    """Alias for should_fetch_dims — kept for backward compatibility."""
    return should_fetch_dims(url, force_retry)
