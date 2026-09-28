"""
pick_tracker_store.py
CRUD for tracked Hot Picks (Prop-Line Edges and TD Chances) and their
eventual hit/miss result - the data behind the Track Record tab.

Mirrors slip_store.py's storage pattern exactly (same Google Sheets
spreadsheet, identified by the ROSTER_SHEET_ID secret - just a different
worksheet tab within it named "tracked_picks" - plus the same local-JSON
fallback and hard timeout on the Sheets calls) so pick history survives a
Streamlit Cloud redeploy the same way saved rosters and bet slips do, and
so a slow/misconfigured Sheets connection can never hang the whole app.
"""

import json
import os
import threading
from datetime import datetime, timezone

LOCAL_FALLBACK_PATH = "data/tracked_picks_local.json"

CATEGORIES = ["edge", "td_anytime", "td_first"]
STATUSES = ["Pending", "Hit", "Miss", "Push"]

_SHEETS_TIMEOUT_SECONDS = 15
_last_error = None

_COLUMNS = [
    "id", "created_at", "season", "week", "category",
    "player", "player_id", "team", "position",
    "detail_json", "game_id", "kickoff",
    "status", "actual_json", "resolved_at",
]


def _with_timeout(fn, *args, default=None):
    """Same daemon-thread timeout wrapper as roster_store._with_timeout -
    duplicated rather than imported so this module has no hard dependency
    on roster_store (and can't break if that file changes shape)."""
    box = {}

    def runner():
        try:
            box["result"] = fn(*args)
        except Exception:
            pass

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout=_SHEETS_TIMEOUT_SECONDS)
    if "result" not in box:
        return default
    return box["result"]


def _local_load() -> list:
    try:
        with open(LOCAL_FALLBACK_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _local_save(picks: list) -> None:
    os.makedirs(os.path.dirname(LOCAL_FALLBACK_PATH), exist_ok=True)
    with open(LOCAL_FALLBACK_PATH, "w") as f:
        json.dump(picks, f, indent=2)


def _sheets_client_uncapped(st_secrets):
    global _last_error
    try:
        service_account_info = st_secrets.get("GOOGLE_SERVICE_ACCOUNT")
        sheet_id = st_secrets.get("ROSTER_SHEET_ID")
        if not service_account_info or not sheet_id:
            _last_error = "GOOGLE_SERVICE_ACCOUNT or ROSTER_SHEET_ID secret is missing."
            return None
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(dict(service_account_info), scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id)
        try:
            worksheet = sheet.worksheet("tracked_picks")
        except Exception:
            worksheet = sheet.add_worksheet(title="tracked_picks", rows=2000, cols=len(_COLUMNS))
            worksheet.append_row(_COLUMNS)
        _last_error = None
        return worksheet
    except Exception as e:
        _last_error = f"{type(e).__name__}: {e}"
        return None


def _sheets_client(st_secrets):
    global _last_error
    _TIMED_OUT = object()
    result = _with_timeout(_sheets_client_uncapped, st_secrets, default=_TIMED_OUT)
    if result is _TIMED_OUT:
        _last_error = f"Connection attempt did not finish within {_SHEETS_TIMEOUT_SECONDS}s (timed out)."
        return None
    return result


def last_connection_error():
    return _last_error


def _row_to_pick(r: dict) -> dict | None:
    try:
        return {
            "id": r["id"],
            "created_at": r.get("created_at", ""),
            "season": int(r["season"]),
            "week": int(r["week"]),
            "category": r.get("category", "edge"),
            "player": r.get("player", ""),
            "player_id": r.get("player_id", ""),
            "team": r.get("team", ""),
            "position": r.get("position", ""),
            "detail": json.loads(r["detail_json"]) if r.get("detail_json") else {},
            "game_id": r.get("game_id", ""),
            "kickoff": r.get("kickoff", ""),
            "status": r.get("status") or "Pending",
            "actual": json.loads(r["actual_json"]) if r.get("actual_json") else {},
            "resolved_at": r.get("resolved_at", ""),
        }
    except Exception:
        return None  # skip a malformed row rather than fail the whole load


def _sheets_load(worksheet) -> list:
    records = worksheet.get_all_records()
    picks = [_row_to_pick(r) for r in records]
    return [p for p in picks if p is not None]


def _pick_to_row(p: dict) -> list:
    return [
        p["id"], p.get("created_at", ""), p.get("season", ""), p.get("week", ""), p.get("category", "edge"),
        p.get("player", ""), p.get("player_id", ""), p.get("team", ""), p.get("position", ""),
        json.dumps(p.get("detail", {})), p.get("game_id", ""), p.get("kickoff", ""),
        p.get("status", "Pending"), json.dumps(p.get("actual", {})), p.get("resolved_at", ""),
    ]


def _sheets_save(worksheet, picks: list) -> None:
    """Rewrites the whole sheet in ONE batched API call instead of one
    append_row() per row (the previous approach). That N+1-call pattern
    is what actually caused tracked results to vanish: every save was
    wrapped in an 8-second hard timeout (_SHEETS_TIMEOUT_SECONDS), and
    once the pick history grew past a couple dozen rows - trivial after
    a few weeks of a full slate - the sequential round-trips blew past
    8 seconds, the daemon thread got abandoned mid-write (sometimes
    after clear() had already wiped the sheet but before the rewrite
    finished), and the save silently fell through to the local,
    redeploy-losing fallback file. A single update() call for the whole
    grid stays well under the timeout no matter how much history has
    piled up, and it can't leave the sheet half-written."""
    worksheet.clear()
    rows = [_COLUMNS] + [_pick_to_row(p) for p in picks]
    worksheet.update(rows)


def load_picks(st_secrets) -> list:
    """Every tracked pick, newest first. Never raises - falls back to
    local storage (or an empty list on first-ever run) if Sheets isn't
    reachable."""
    worksheet = _sheets_client(st_secrets)
    picks = None
    if worksheet is not None:
        picks = _with_timeout(_sheets_load, worksheet, default=None)
    if picks is None:
        picks = _local_load()
    return sorted(picks, key=lambda p: p.get("created_at", ""), reverse=True)


def _save_all(st_secrets, picks: list) -> None:
    worksheet = _sheets_client(st_secrets)
    if worksheet is not None:
        _FAILED = object()
        result = _with_timeout(lambda: _sheets_save(worksheet, picks) or True, default=_FAILED)
        if result is not _FAILED:
            return
    _local_save(picks)


def add_picks(st_secrets, new_picks: list) -> None:
    """Append a batch of freshly-snapshotted picks (e.g. a whole week's
    worth of Prop Edges + TD Chances at once) without disturbing any
    existing rows. Stamps created_at on anything that doesn't already
    have one."""
    if not new_picks:
        return
    existing = load_picks(st_secrets)
    now = datetime.now(timezone.utc).isoformat()
    for p in new_picks:
        if not p.get("created_at"):
            p["created_at"] = now
    _save_all(st_secrets, existing + new_picks)


def update_picks(st_secrets, updated_picks: list) -> None:
    """Overwrite specific picks by id (e.g. moving them from Pending to
    Hit/Miss/Push once their game is final) - everything else in the
    store is left untouched."""
    if not updated_picks:
        return
    by_id = {p["id"]: p for p in updated_picks}
    existing = load_picks(st_secrets)
    merged = [by_id.get(p["id"], p) for p in existing]
    _save_all(st_secrets, merged)


def season_week_already_tracked(st_secrets, season: int, week: int) -> bool:
    """True if this (season, week) already has at least one snapshotted
    pick - the automatic once-per-week snapshot checks this first so a
    page reload never double-tracks the same week's picks."""
    existing = load_picks(st_secrets)
    return any(p.get("season") == season and p.get("week") == week for p in existing)


def using_local_fallback(st_secrets) -> bool:
    return _sheets_client(st_secrets) is None
