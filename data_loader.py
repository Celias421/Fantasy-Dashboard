"""
data_loader.py
Shared logic for finding current starters and loading their weekly
stats. Used by dashboard.py both locally and when hosted on Streamlit
Community Cloud.

Uses nflreadpy, which downloads official data files from the nflverse
project (not scraped from a website), so it's reliable and not subject
to bot-blocking.
"""

import requests
import nflreadpy as nfl
import pandas as pd

from config import SEASONS, CURRENT_SEASON, STARTERS_PER_POSITION, PROP_MARKET_MAP, ANYTIME_TD_MARKET

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


def load_prop_lines(api_key: str):
    """Current player prop lines from The Odds API: (props_df, td_df).

    props_df covers the point-value markets in PROP_MARKET_MAP (yards,
    passing TDs) with columns [player, market, point] - the season
    average gets compared directly against these.

    td_df covers ANYTIME_TD_MARKET separately, with columns
    [player, implied_prob] (0-100, averaged across bookmakers) - this is
    a yes/no "does this player score" market priced as odds, not a line,
    so it can't be compared to a season average the same way and is kept
    apart from props_df on purpose.

    Both come from the same API calls (one per event), so pulling this
    extra market doesn't cost any additional requests - just a few more
    credits per event since the markets list is longer.

    Returns two empty DataFrames (never raises) if the key is missing,
    invalid, or the API is unreachable - callers should treat missing
    prop data as normal, not a crash."""
    prop_columns = ["player", "market", "point"]
    td_columns = ["player", "implied_prob"]
    if not api_key:
        return pd.DataFrame(columns=prop_columns), pd.DataFrame(columns=td_columns)

    try:
        events_resp = requests.get(
            "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/",
            params={"apiKey": api_key},
            timeout=15,
        )
        events_resp.raise_for_status()
        events = events_resp.json()
    except Exception:
        return pd.DataFrame(columns=prop_columns), pd.DataFrame(columns=td_columns)

    markets = ",".join(list(PROP_MARKET_MAP.values()) + [ANYTIME_TD_MARKET])
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
        except Exception:
            continue

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
                        td_rows.append({"player": player_name, "implied_prob": _implied_probability(price)})
                    else:
                        # Each player prop market has two outcomes (Over/Under)
                        # per player; we only need the line itself, which is
                        # the same for both, so keep the first one seen.
                        point = outcome.get("point")
                        if point is None:
                            continue
                        prop_rows.append({"player": player_name, "market": market_key, "point": point})
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

    return props, td


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
