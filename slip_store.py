"""
slip_store.py
CRUD for tracked bet slips (sportsbook, legs, odds, stake, result) - the
data behind the Bet Slip Tracker tab.

Mirrors roster_store.py's storage pattern exactly (same Google Sheets
spreadsheet, identified by the ROSTER_SHEET_ID secret - just a different
worksheet tab within it named "bet_slips" - plus the same local-JSON
fallback and hard timeout on the Sheets calls) so bet slip history
survives a Streamlit Cloud redeploy the same way saved rosters do, and so
a slow/misconfigured Sheets connection can never hang the whole app.
"""

import json
import os
import threading
from datetime import datetime, timezone

LOCAL_FALLBACK_PATH = "data/bet_slips_local.json"

SPORTSBOOKS = [
    "DraftKings", "FanDuel", "BetMGM", "Caesars", "ESPN BET",
    "Fanatics", "bet365", "PointsBet", "Other",
]
BET_TYPES = ["Single", "Parlay", "Same Game Parlay", "Teaser", "Round Robin", "Other"]
RESULTS = ["Pending", "Won", "Lost", "Push", "Cashed Out"]

_SHEETS_TIMEOUT_SECONDS = 8
_last_error = None

_COLUMNS = [
    "id", "created_at", "date", "sportsbook", "bet_type", "legs_json",
    "odds", "stake", "potential_payout", "result", "actual_payout", "notes",
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


def _local_save(slips: list) -> None:
    os.makedirs(os.path.dirname(LOCAL_FALLBACK_PATH), exist_ok=True)
    with open(LOCAL_FALLBACK_PATH, "w") as f:
        json.dump(slips, f, indent=2)


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
            worksheet = sheet.worksheet("bet_slips")
        except Exception:
            worksheet = sheet.add_worksheet(title="bet_slips", rows=200, cols=len(_COLUMNS))
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


def _row_to_slip(r: dict) -> dict | None:
    try:
        return {
            "id": r["id"],
            "created_at": r.get("created_at", ""),
            "date": r.get("date", ""),
            "sportsbook": r.get("sportsbook", "Other"),
            "bet_type": r.get("bet_type", "Single"),
            "legs": json.loads(r["legs_json"]) if r.get("legs_json") else [],
            "odds": r.get("odds", ""),
            "stake": float(r["stake"]) if r.get("stake") not in (None, "") else 0.0,
            "potential_payout": float(r["potential_payout"]) if r.get("potential_payout") not in (None, "") else 0.0,
            "result": r.get("result") or "Pending",
            "actual_payout": float(r["actual_payout"]) if r.get("actual_payout") not in (None, "") else 0.0,
            "notes": r.get("notes", ""),
        }
    except Exception:
        return None  # skip a malformed row rather than fail the whole load


def _sheets_load(worksheet) -> list:
    records = worksheet.get_all_records()
    slips = [_row_to_slip(r) for r in records]
    return [s for s in slips if s is not None]


def _slip_to_row(s: dict) -> list:
    return [
        s["id"], s.get("created_at", ""), s.get("date", ""), s.get("sportsbook", "Other"),
        s.get("bet_type", "Single"), json.dumps(s.get("legs", [])), s.get("odds", ""),
        s.get("stake", 0.0), s.get("potential_payout", 0.0), s.get("result", "Pending"),
        s.get("actual_payout", 0.0), s.get("notes", ""),
    ]


def _sheets_save(worksheet, slips: list) -> None:
    worksheet.clear()
    worksheet.append_row(_COLUMNS)
    for s in slips:
        worksheet.append_row(_slip_to_row(s))


def load_slips(st_secrets) -> list:
    """Every tracked bet slip, newest first. Never raises - falls back to
    local storage (or an empty list on first-ever run) if Sheets isn't
    reachable."""
    worksheet = _sheets_client(st_secrets)
    slips = None
    if worksheet is not None:
        slips = _with_timeout(_sheets_load, worksheet, default=None)
    if slips is None:
        slips = _local_load()
    return sorted(slips, key=lambda s: s.get("created_at", ""), reverse=True)


def save_slip(st_secrets, slip: dict) -> tuple:
    """Create or update one slip (matched by slip['id']). Returns
    (success: bool, message: str) - never raises."""
    slips = load_slips(st_secrets)
    slips = [s for s in slips if s["id"] != slip["id"]]
    if "created_at" not in slip or not slip["created_at"]:
        slip["created_at"] = datetime.now(timezone.utc).isoformat()
    slips.append(slip)

    worksheet = _sheets_client(st_secrets)
    if worksheet is not None:
        _FAILED = object()
        result = _with_timeout(lambda: _sheets_save(worksheet, slips) or True, default=_FAILED)
        if result is not _FAILED:
            return True, "Saved."
    _local_save(slips)
    return True, "Saved locally (Google Sheets isn't configured yet - see README - so this won't survive a redeploy)."


def delete_slip(st_secrets, slip_id: str) -> None:
    slips = [s for s in load_slips(st_secrets) if s["id"] != slip_id]
    worksheet = _sheets_client(st_secrets)
    if worksheet is not None:
        _FAILED = object()
        result = _with_timeout(lambda: _sheets_save(worksheet, slips) or True, default=_FAILED)
        if result is not _FAILED:
            return
    _local_save(slips)


def using_local_fallback(st_secrets) -> bool:
    return _sheets_client(st_secrets) is None
