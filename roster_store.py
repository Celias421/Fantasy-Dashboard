"""
roster_store.py
CRUD for up to 10 named, saved fantasy rosters (list of player names each).

Backed by Google Sheets when the GOOGLE_SERVICE_ACCOUNT + ROSTER_SHEET_ID
secrets are configured (see README setup section) - that's what makes
rosters survive a Streamlit Cloud redeploy, unlike anything written to the
app's own local disk (which gets wiped on every git push, the same issue
we hit with the odds cache). Falls back to a local JSON file when those
secrets aren't set, so the feature is fully usable/testable before the
Google Sheets setup is done - just without cross-redeploy persistence.
"""

import json
import os
import threading

MAX_ROSTERS = 10
LOCAL_FALLBACK_PATH = "data/rosters_local.json"

# Hard ceiling on how long we'll wait for Google Sheets before giving up and
# falling back to local storage. Without this, a slow/hanging network call
# to Google's API (bad credentials, DNS hiccup, Google-side slowness) would
# block the ENTIRE app from ever loading - which is exactly what happened on
# the first deploy after adding this feature: the app defaults to showing
# the Fantasy Lineups side on every load, which immediately calls into this
# module, so a hang here is a hang for every single visitor, forever.
_SHEETS_TIMEOUT_SECONDS = 8


def _with_timeout(fn, *args, default=None):
    """Runs fn(*args) on a background thread and gives up after
    _SHEETS_TIMEOUT_SECONDS, returning `default` instead of hanging. Uses a
    plain daemon thread (not a ThreadPoolExecutor) specifically so that if
    the call truly never returns, the leftover thread can't block process
    shutdown or pile up as a permanently-alive non-daemon thread - it just
    gets abandoned and garbage collected whenever it eventually finishes."""
    box = {}

    def runner():
        try:
            box["result"] = fn(*args)
        except Exception:
            pass  # leave box empty -> caller sees the timeout/failure default

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


def _local_save(rosters: list) -> None:
    os.makedirs(os.path.dirname(LOCAL_FALLBACK_PATH), exist_ok=True)
    with open(LOCAL_FALLBACK_PATH, "w") as f:
        json.dump(rosters, f, indent=2)


def _sheets_client_uncapped(st_secrets):
    """The real logic - see _sheets_client below for why this is never
    called directly. Can block for an arbitrary amount of time on the
    gspread.authorize()/open_by_key() network calls if Google's API (or the
    credentials) are slow/misconfigured; callers must go through the
    timeout wrapper."""
    try:
        service_account_info = st_secrets.get("GOOGLE_SERVICE_ACCOUNT")
        sheet_id = st_secrets.get("ROSTER_SHEET_ID")
        if not service_account_info or not sheet_id:
            return None
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(dict(service_account_info), scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id)
        try:
            worksheet = sheet.worksheet("rosters")
        except Exception:
            worksheet = sheet.add_worksheet(title="rosters", rows=20, cols=3)
            worksheet.append_row(["id", "name", "players_json"])
        return worksheet
    except Exception:
        return None


def _sheets_client(st_secrets):
    """Returns an authorized gspread client + worksheet, or None if the
    Google Sheets secrets aren't configured OR the connection attempt took
    too long (see _SHEETS_TIMEOUT_SECONDS above). Kept as a function (not
    module-level) so it's only attempted when actually needed, and so a
    misconfigured/missing/slow credential never hangs or crashes app
    startup - the local fallback silently takes over instead."""
    return _with_timeout(_sheets_client_uncapped, st_secrets, default=None)


def _sheets_load(worksheet) -> list:
    records = worksheet.get_all_records()  # list of dicts keyed by header row
    rosters = []
    for r in records:
        try:
            rosters.append({
                "id": r["id"],
                "name": r["name"],
                "players": json.loads(r["players_json"]) if r.get("players_json") else [],
            })
        except Exception:
            continue  # skip a malformed row rather than fail the whole load
    return rosters


def _sheets_save(worksheet, rosters: list) -> None:
    worksheet.clear()
    worksheet.append_row(["id", "name", "players_json"])
    for r in rosters:
        worksheet.append_row([r["id"], r["name"], json.dumps(r["players"])])


def load_rosters(st_secrets) -> list:
    """[{"id": str, "name": str, "players": [str, ...]}, ...] - up to
    MAX_ROSTERS entries. Never raises; an unreachable/misconfigured Sheet
    falls back to the local file (or an empty list on first-ever run)."""
    worksheet = _sheets_client(st_secrets)
    if worksheet is not None:
        rosters = _with_timeout(_sheets_load, worksheet, default=None)
        if rosters is not None:
            return rosters
        # fall through to local - either the call raised or it timed out
    return _local_load()


def save_roster(st_secrets, roster_id: str, name: str, players: list) -> tuple:
    """Create or update one roster (matched by roster_id). Returns
    (success: bool, message: str) - never raises, so a save failure shows
    a message instead of crashing the page mid-edit."""
    rosters = load_rosters(st_secrets)
    existing_ids = {r["id"] for r in rosters}
    if roster_id not in existing_ids and len(rosters) >= MAX_ROSTERS:
        return False, f"You already have {MAX_ROSTERS} saved rosters - delete one before adding another."

    rosters = [r for r in rosters if r["id"] != roster_id]
    rosters.append({"id": roster_id, "name": name, "players": players})

    worksheet = _sheets_client(st_secrets)
    if worksheet is not None:
        _FAILED = object()  # sentinel - _sheets_save's real return is None on success
        result = _with_timeout(lambda: _sheets_save(worksheet, rosters) or True, default=_FAILED)
        if result is not _FAILED:
            return True, "Saved."
        # fall through to local so the edit isn't lost outright
    _local_save(rosters)
    return True, "Saved locally (Google Sheets isn't configured yet - see README - so this won't survive a redeploy)."


def delete_roster(st_secrets, roster_id: str) -> None:
    rosters = [r for r in load_rosters(st_secrets) if r["id"] != roster_id]
    worksheet = _sheets_client(st_secrets)
    if worksheet is not None:
        _FAILED = object()
        result = _with_timeout(lambda: _sheets_save(worksheet, rosters) or True, default=_FAILED)
        if result is not _FAILED:
            return
    _local_save(rosters)


def using_local_fallback(st_secrets) -> bool:
    """True if rosters are currently being stored locally (won't survive
    a redeploy) rather than in Google Sheets - used to show a warning."""
    return _sheets_client(st_secrets) is None
