"""
data_loader.py
Shared logic for finding current starters and loading their weekly
stats. Used by dashboard.py both locally and when hosted on Streamlit
Community Cloud.

Uses nflreadpy, which downloads official data files from the nflverse
project (not scraped from a website), so it's reliable and not subject
to bot-blocking.
"""

import datetime
import json
import os

import requests
import nflreadpy as nfl
import pandas as pd

from config import (
    SEASONS, CURRENT_SEASON, STARTERS_PER_POSITION, PROP_MARKET_MAP,
    ANYTIME_TD_MARKET, ODDS_API_SAFETY_BUFFER,
)

# Where the last successful prop-lines pull is saved on disk, independent
# of Streamlit's own st.cache_data cache. Streamlit's cache resets whenever
# the cached function's source code changes - which happened repeatedly
# during this feature's development and forced a fresh API pull on every
# code push, even though nothing about "how old is the data" had changed.
# This file's timestamp is the real source of truth for that question, so
# it survives code changes and app restarts (though not a brand new
# Streamlit Cloud deploy, which rebuilds the filesystem from scratch - see
# load_prop_lines_with_cache).
PROP_LINES_CACHE_PATH = "data/prop_lines_cache.json"

KEEP_COLUMNS = [
    "player_display_name", "position", "team", "opponent_team",
    "season", "week",
    "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
    "carries", "rushing_yards", "rushing_tds",
    "targets", "receptions", "receiving_yards", "receiving_tds",
    "fantasy_points", "fantasy_points_ppr",
]


def get_current_starters() -> set:
    """Return the set of current starter names across all teams, based on
    the most recent depth chart snapshot (current season only - rosters
    from prior seasons aren't relevant to who's starting now) and
    STARTERS_PER_POSITION."""
    dc = nfl.load_depth_charts([CURRENT_SEASON]).to_pandas()
    latest = dc[dc["dt"] == dc["dt"].max()]

    names = set()
    for pos, n in STARTERS_PER_POSITION.items():
        sub = latest[(latest["pos_abb"] == pos) & (latest["pos_rank"] <= n)]
        names.update(sub["player_name"])
    return names


def load_player_meta() -> pd.DataFrame:
    """Player-level display info: headshot photo and team colors/logo.
    Uses the current season's roster. Deliberately excludes 'team' - the
    stats data already has that column, and merging two 'team' columns
    would cause a naming collision."""
    rosters = nfl.load_rosters([CURRENT_SEASON]).to_pandas()
    teams = nfl.load_teams().to_pandas()

    meta = rosters[["full_name", "team", "headshot_url"]].rename(columns={"full_name": "player"})
    meta = meta.merge(
        teams[["team_abbr", "team_color", "team_color2", "team_logo_espn"]],
        left_on="team", right_on="team_abbr", how="left",
    )
    meta = meta.drop(columns=["team", "team_abbr"])
    return meta.drop_duplicates(subset="player")


def load_team_meta() -> pd.DataFrame:
    """Team-level display info: logo and colors, one row per team. Unlike
    load_player_meta (which is keyed on player and only covers teams with
    a tracked starter), this covers every team - needed for things like a
    full week's schedule table where a team can appear with no tracked
    players."""
    teams = nfl.load_teams().to_pandas()
    return teams[["team_abbr", "team_logo_espn", "team_color", "team_color2"]]


def load_defense_ranks() -> pd.DataFrame:
    """Rank every team's defense (1 = toughest, allows the least) against
    each offensive position, for the stats that matter for prop research.
    Based on current-season totals only, since prior years' defenses may
    have completely different personnel."""
    df = nfl.load_player_stats([CURRENT_SEASON]).to_pandas()
    relevant = df[df["position"].isin(["QB", "RB", "WR", "TE"])]

    stat_cols = [
        c for c in ["passing_yards", "passing_tds", "rushing_yards", "rushing_tds",
                    "receiving_yards", "receiving_tds", "receptions", "fantasy_points_ppr"]
        if c in relevant.columns
    ]

    allowed = (
        relevant.groupby(["position", "opponent_team"])[stat_cols]
        .sum()
        .reset_index()
        .rename(columns={"opponent_team": "team"})
    )

    for stat in stat_cols:
        # Rank 1 = allows the fewest = toughest matchup for that stat
        allowed[f"{stat}_rank"] = allowed.groupby("position")[stat].rank(method="min", ascending=True).astype(int)

    return allowed


def load_schedule() -> pd.DataFrame:
    """This season's full schedule: matchups, dates, and betting lines."""
    return nfl.load_schedules([CURRENT_SEASON]).to_pandas()


def load_all_seasons_schedule() -> pd.DataFrame:
    """Just season/week/home_team/away_team, across every season in
    SEASONS (not just current) - used to know home/away for historical
    games in chart tooltips, which load_schedule() alone can't answer
    since it's scoped to the current season only."""
    sched = nfl.load_schedules(SEASONS).to_pandas()
    return sched[["season", "week", "home_team", "away_team"]]


def load_starter_stats() -> pd.DataFrame:
    """Download weekly stats across all configured SEASONS (e.g. all of
    last season plus this season as it progresses), filtered to current
    starters. A 'season' column distinguishes which year each row is
    from - use it when computing current-season-only numbers."""
    starter_names = get_current_starters()

    df = nfl.load_player_stats(SEASONS).to_pandas()
    df = df[[c for c in KEEP_COLUMNS if c in df.columns]]

    filtered = df[df["player_display_name"].isin(starter_names)].copy()
    filtered = filtered.rename(columns={"player_display_name": "player"})
    filtered = filtered.sort_values(["season", "week"])
    return filtered


def load_current_injuries() -> pd.DataFrame:
    """The most recent week's official NFL injury report: status
    (Out / Doubtful / Questionable), primary injury, and practice
    participation. Sourced from nflverse's copy of the official reports
    teams submit - not scraped, not social media. Covers every player on
    a report, not just tracked starters - the Injuries page uses that
    fuller view."""
    inj = nfl.load_injuries([CURRENT_SEASON]).to_pandas()
    if inj.empty:
        return inj
    latest_week = inj["week"].max()
    latest = inj[inj["week"] == latest_week].copy()
    latest = latest.rename(columns={"full_name": "player"})
    cols = ["player", "team", "position", "week", "report_status", "report_primary_injury", "practice_status"]
    return latest[[c for c in cols if c in latest.columns]]


def _implied_probability(american_odds: float) -> float:
    """Convert American odds (e.g. -140, +150) to an implied probability
    (0-1). This is the sportsbook's priced-in probability INCLUDING their
    margin/vig - it will always run a bit higher than the "true" chance,
    but it's the standard, honest way to turn odds into a percentage."""
    if american_odds >= 0:
        return 100.0 / (american_odds + 100.0)
    return (-american_odds) / (-american_odds + 100.0)


def _quota_from_headers(resp) -> dict:
    """Pull the Odds API's own quota-tracking headers off a response.
    These are the source of truth for usage - the API tells us directly
    how many credits are left and how many we've used since the last
    monthly reset, so there's no need to keep our own running estimate
    (which could drift out of sync with reality)."""
    remaining = resp.headers.get("x-requests-remaining")
    used = resp.headers.get("x-requests-used")
    try:
        remaining = int(remaining) if remaining is not None else None
    except ValueError:
        remaining = None
    try:
        used = int(used) if used is not None else None
    except ValueError:
        used = None
    return {"remaining": remaining, "used": used}


def load_prop_lines(api_key: str):
    """Current player prop lines from The Odds API: (props_df, td_df, quota).

    props_df covers the point-value markets in PROP_MARKET_MAP (yards,
    passing TDs) with columns [player, market, point] - the season
    average gets compared directly against these.

    td_df covers ANYTIME_TD_MARKET separately, with columns
    [player, implied_prob] (0-100, averaged across bookmakers) - this is
    a yes/no "does this player score" market priced as odds, not a line,
    so it can't be compared to a season average the same way and is kept
    apart from props_df on purpose.

    quota is {"remaining": int|None, "used": int|None, "skipped": bool} -
    "remaining"/"used" come straight from the API's own response headers
    (the authoritative source), and "skipped" is True if we deliberately
    didn't pull odds this cycle because there wasn't enough quota headroom.

    Both prop markets and the TD market come from the same API calls (one
    per event), so pulling the extra market doesn't cost any additional
    requests - just a few more credits per event since the markets list
    is longer. Each event's odds call costs (markets requested) x (regions
    requested) credits, per The Odds API's own pricing - with 5 markets
    and 1 region that's 5 credits per game.

    Before spending anything on odds, this checks the quota remaining
    after the (cheap/free) events listing call. If pulling odds for every
    event found would use up more than ODDS_API_SAFETY_BUFFER credits of
    headroom, it skips the odds pulls entirely for this cycle rather than
    risk running the account down to zero - the next cached refresh (up
    to 24h later) will try again.

    Returns two empty DataFrames (never raises) if the key is missing,
    invalid, or the API is unreachable - callers should treat missing
    prop data as normal, not a crash."""
    prop_columns = ["player", "market", "point"]
    td_columns = ["player", "implied_prob"]
    empty_quota = {"remaining": None, "used": None, "skipped": False}
    if not api_key:
        return pd.DataFrame(columns=prop_columns), pd.DataFrame(columns=td_columns), empty_quota

    try:
        events_resp = requests.get(
            "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/",
            params={"apiKey": api_key},
            timeout=15,
        )
        events_resp.raise_for_status()
        events = events_resp.json()
        quota = _quota_from_headers(events_resp)
    except Exception:
        return pd.DataFrame(columns=prop_columns), pd.DataFrame(columns=td_columns), empty_quota

    markets_list = list(PROP_MARKET_MAP.values()) + [ANYTIME_TD_MARKET]
    markets = ",".join(markets_list)

    # Pre-flight safety check: estimate the worst-case cost of pulling odds
    # for every event we found, and skip entirely if that would eat into
    # the safety buffer. Worst case = every market present for every event.
    estimated_cost = len(events) * len(markets_list)
    if quota["remaining"] is not None and quota["remaining"] - estimated_cost < ODDS_API_SAFETY_BUFFER:
        quota["skipped"] = True
        return pd.DataFrame(columns=prop_columns), pd.DataFrame(columns=td_columns), quota

    prop_rows = []
    td_rows = []
    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue
        try:
            odds_resp = requests.get(
                f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{event_id}/odds",
                params={"apiKey": api_key, "regions": "us", "markets": markets, "oddsFormat": "american"},
                timeout=15,
            )
            odds_resp.raise_for_status()
            event_odds = odds_resp.json()
            quota = _quota_from_headers(odds_resp)
        except Exception:
            continue

        # Mid-loop safety check too, in case the pre-flight estimate was
        # off (e.g. more markets came back per event than expected) - stop
        # pulling further events rather than let the buffer get eaten into.
        if quota["remaining"] is not None and quota["remaining"] < ODDS_API_SAFETY_BUFFER:
            quota["skipped"] = True
            break

        # This parsing loop used to sit outside any try/except. It's reading
        # the shape of a third-party API response, and one event with an
        # unexpected/malformed payload (a market with no outcomes, a price
        # that isn't a plain number, etc.) would raise uncaught and crash
        # the whole page - this function runs at the top of every page
        # load, so that took the entire app down, not just this feature.
        # Skipping just the one bad event keeps everything else working.
        try:
            for bookmaker in event_odds.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    market_key = market.get("key")
                    for outcome in market.get("outcomes", []):
                        player_name = outcome.get("description")
                        if player_name is None:
                            continue
                        if market_key == ANYTIME_TD_MARKET:
                            price = outcome.get("price")
                            if price is None:
                                continue
                            try:
                                implied = _implied_probability(float(price))
                            except (TypeError, ValueError):
                                continue
                            td_rows.append({"player": player_name, "implied_prob": implied})
                        else:
                            # Each player prop market has two outcomes (Over/Under)
                            # per player; we only need the line itself, which is
                            # the same for both, so keep the first one seen.
                            point = outcome.get("point")
                            if point is None:
                                continue
                            try:
                                point = float(point)
                            except (TypeError, ValueError):
                                continue
                            prop_rows.append({"player": player_name, "market": market_key, "point": point})
        except Exception:
            continue
        # Stop once we've pulled odds for every scheduled event this call found

    if prop_rows:
        props = pd.DataFrame(prop_rows)
        # Multiple bookmakers may list the same player/market - average their lines
        props = props.groupby(["player", "market"], as_index=False)["point"].mean()
    else:
        props = pd.DataFrame(columns=prop_columns)

    if td_rows:
        td = pd.DataFrame(td_rows)
        # Multiple bookmakers may list the same player - average their
        # implied probabilities (not the raw odds, which don't average sensibly)
        td = td.groupby("player", as_index=False)["implied_prob"].mean()
        td["implied_prob"] = (td["implied_prob"] * 100).round(1)
    else:
        td = pd.DataFrame(columns=td_columns)

    return props, td, quota


def _read_prop_lines_cache_file():
    """Best-effort read of the on-disk prop-lines cache file. Returns None
    if it doesn't exist yet (first run, or after a fresh deploy) or is
    unreadable/corrupt - callers treat that exactly like "no cache yet",
    never a crash."""
    try:
        with open(PROP_LINES_CACHE_PATH, "r") as f:
            raw = json.load(f)
        return {
            "props_df": pd.DataFrame(raw["props"]),
            "td_df": pd.DataFrame(raw["td"]),
            "quota": raw["quota"],
            "pulled_at": datetime.datetime.fromisoformat(raw["pulled_at"]),
        }
    except Exception:
        return None


def _write_prop_lines_cache_file(props_df: pd.DataFrame, td_df: pd.DataFrame, quota: dict, pulled_at: datetime.datetime) -> None:
    """Best-effort write. If this fails (e.g. a read-only filesystem) we
    just lose the cross-restart fallback for this cycle - degraded, not
    fatal, since load_prop_lines_with_cache still works, it'll just call
    the API a bit more than ideal until a write succeeds."""
    try:
        os.makedirs(os.path.dirname(PROP_LINES_CACHE_PATH), exist_ok=True)
        payload = {
            "props": props_df.to_dict(orient="records"),
            "td": td_df.to_dict(orient="records"),
            "quota": quota,
            "pulled_at": pulled_at.isoformat(),
        }
        with open(PROP_LINES_CACHE_PATH, "w") as f:
            json.dump(payload, f)
    except Exception:
        pass


def load_prop_lines_with_cache(api_key: str):
    """Same data as load_prop_lines(), but pulls from the Odds API at most
    once every 24 hours no matter what - even across app restarts and
    even if Streamlit's own st.cache_data cache gets reset by a code
    change (its cache key is tied to the cached function's source, so
    every code edit forces an immediate re-pull under that alone - this
    is what repeatedly burned API quota while this feature was being
    built). It works by keeping its own on-disk, timestamp-based cache
    file that doesn't care whether the code changed - only how old the
    last successful pull actually is.

    Returns (props_df, td_df, quota, pulled_at, stale):
    - pulled_at: when this data was actually fetched from the API (not
      necessarily just now - could be from the on-disk cache).
    - stale: True if this is a fallback to the last known good pull,
      served because a fresh pull was skipped (quota safety buffer) or
      failed outright, even though it's past the normal 24h window -
      showing yesterday's real odds beats showing nothing.

    Caveat: this cache file lives on the app's running container, not in
    the git repo, so a brand new Streamlit Cloud deploy starts with no
    file and will always cost one fresh pull the first time it's opened
    after that deploy - no caching strategy can avoid that, since a new
    deploy is a brand new filesystem. This only guarantees the "at most
    once a day" behavior within a running deployment."""
    cached = _read_prop_lines_cache_file()
    now = datetime.datetime.now()

    if cached and (now - cached["pulled_at"]) < datetime.timedelta(hours=24):
        return cached["props_df"], cached["td_df"], cached["quota"], cached["pulled_at"], False

    props_df, td_df, quota = load_prop_lines(api_key)

    # Only fall back to the stale cache when the fresh attempt didn't
    # really tell us anything new: either it was deliberately skipped to
    # protect the quota buffer, or the initial events call itself failed
    # (quota["remaining"] stays None only when that call errored, given we
    # do have a key - a successful pull that legitimately found zero
    # events, e.g. a bye week, still reports a real remaining count and
    # should be trusted and cached as current, not treated as a failure).
    fresh_pull_uninformative = quota.get("skipped") or (bool(api_key) and quota.get("remaining") is None)
    if cached and fresh_pull_uninformative:
        return cached["props_df"], cached["td_df"], cached["quota"], cached["pulled_at"], True

    _write_prop_lines_cache_file(props_df, td_df, quota, now)
    return props_df, td_df, quota, now, False


def geocode_city(city: str):
    """(latitude, longitude) for a city name via Open-Meteo's free
    geocoding API (no key required). Returns None if the city can't be
    resolved or the API is unreachable - callers should treat missing
    coordinates as normal (e.g. skip showing weather), not a crash."""
    try:
        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results")
        if not results:
            return None
        return (results[0]["latitude"], results[0]["longitude"])
    except Exception:
        return None


def load_game_weather(lat: float, lon: float, game_date: str):
    """Forecast high/low temp, max wind, and rain chance for a specific
    calendar date (YYYY-MM-DD) at a location, via Open-Meteo (free, no
    API key). Returns None if that date is outside Open-Meteo's ~16-day
    forecast window or the API is unreachable - a game too far out
    simply doesn't have a forecast yet, which is normal, not an error."""
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "forecast_days": 16,
                "timezone": "auto",
            },
            timeout=10,
        )
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        dates = daily.get("time", [])
        if game_date not in dates:
            return None
        idx = dates.index(game_date)
        return {
            "temp_high": daily["temperature_2m_max"][idx],
            "temp_low": daily["temperature_2m_min"][idx],
            "wind_mph": daily["wind_speed_10m_max"][idx],
            "precip_chance": daily["precipitation_probability_max"][idx],
        }
    except Exception:
        return None
