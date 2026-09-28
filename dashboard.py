"""
dashboard.py
Run locally with: streamlit run dashboard.py
Also the entry point when hosted on Streamlit Community Cloud.
"""

import datetime
import uuid

import altair as alt
import pandas as pd
import streamlit as st

import roster_store
import slip_parser
import slip_store
import theme
from schedule_logic import (
    FLEX_ELIGIBLE,
    SLOT_ORDER,
    build_lineup_slots,
    build_next_opponent_map,
    build_team_implied_totals,
    format_gametime,
    format_kickoff,
    matchup_adjustment,
    optimize_lineup,
    teams_playing_this_week,
)
from config import CURRENT_SEASON, PROP_MARKET_MAP, TEAM_CITY, INDOOR_ROOF_STATES, ODDS_API_SAFETY_BUFFER
from data_loader import (
    load_starter_stats, load_player_meta, load_team_meta, load_defense_ranks, load_schedule,
    load_current_injuries, load_prop_lines_with_cache, geocode_city, load_game_weather,
    load_all_seasons_schedule, clear_nflverse_cache, load_first_td_scorers, load_snap_counts,
)
import pick_tracker_store

st.set_page_config(page_title="The Prop Shop", layout="wide", page_icon="🏈")

# Brand colors, component styling, and the logo header all live in theme.py
# now, as one source of truth (the .streamlit/config.toml theme block
# covers what Streamlit's native theme engine can reach; inject_css()
# covers the rest, including this app's own player-card/badge/delta
# classes that used to be defined inline right here).
theme.inject_css()
theme.render_header()

PROP_STATS_BY_POSITION = {
    "QB": ["passing_yards", "passing_tds", "rushing_yards", "fantasy_points_ppr"],
    "RB": ["rushing_yards", "rushing_tds", "receiving_yards", "receptions", "fantasy_points_ppr"],
    "WR": ["receiving_yards", "receptions", "receiving_tds", "fantasy_points_ppr"],
    "TE": ["receiving_yards", "receptions", "receiving_tds", "fantasy_points_ppr"],
}

# Fixed-order categorical colors for the 4 offensive positions, used on the
# Hot Picks page's charts (donut + position-colored bars) so a position
# means the same color everywhere on that page. Chosen from a validated
# colorblind-safe 8-hue set (dark-surface step) rather than eyeballed -
# these 4 adjacent pairs clear the CVD separation and normal-vision floors
# on this app's dark background. Order is fixed (never re-sorted by a
# filter), per the "color follows the entity, never its rank" rule.
POSITION_COLORS = theme.POSITION_COLORS

ODDS_API_KEY = st.secrets.get("ODDS_API_KEY", "")


@st.cache_data(ttl=3600)
def get_stats() -> pd.DataFrame:
    return load_starter_stats()


@st.cache_data(ttl=3600)
def get_snap_counts() -> pd.DataFrame:
    return load_snap_counts()


@st.cache_data(ttl=3600 * 6)
def get_meta() -> pd.DataFrame:
    return load_player_meta()


@st.cache_data(ttl=3600 * 6)
def get_defense_ranks() -> pd.DataFrame:
    return load_defense_ranks()


@st.cache_data(ttl=3600 * 24 * 30)  # team logos/colors don't change mid-season
def get_team_meta() -> pd.DataFrame:
    return load_team_meta()


@st.cache_data(ttl=3600)
def get_schedule() -> pd.DataFrame:
    return load_schedule()


@st.cache_data(ttl=3600 * 6)
def get_all_seasons_schedule() -> pd.DataFrame:
    return load_all_seasons_schedule()


@st.cache_data(ttl=1800)
def get_first_td_scorers() -> pd.DataFrame:
    """Cached wrapper around the play-by-play pull that resolves tracked
    'First TD' picks - see load_first_td_scorers's docstring. A shorter
    TTL (30 min) than most of this app's caches since, unlike season
    stats, this can meaningfully change mid-game as new touchdowns are
    scored, and Track Record's "Check results now" button is only useful
    if a refresh actually picks up newly-final games in a reasonable time."""
    return load_first_td_scorers(CURRENT_SEASON)


@st.cache_data(ttl=1800)
def get_injuries() -> pd.DataFrame:
    return load_current_injuries()


@st.cache_data(ttl=3600 * 24, persist="disk")
def _get_prop_lines_with_timestamp():
    """Cached together so the 'last updated' time only changes when the
    data actually refreshes (once a day, or when the user forces it),
    not on every page load. persist="disk" is the important part here:
    Streamlit's default cache lives only in the running process's memory,
    so every time the app is stopped and restarted (common during local
    dev/testing) it would otherwise be wiped and force an immediate
    re-pull no matter what the ttl says. Persisting to disk means the
    24-hour window survives restarts, not just page reloads.

    The actual "don't call the API more than once a day" guarantee now
    lives one layer deeper, in load_prop_lines_with_cache's own on-disk
    timestamp file - that one survives even Streamlit resetting THIS
    cache due to a code change, which is exactly what kept happening
    while this feature was being built. This decorator is just a fast
    path so a normal page rerun doesn't even need to re-read that file."""
    empty_quota = {"remaining": None, "used": None, "skipped": False}
    try:
        props_df, td_df, first_td_df, quota, pulled_at, stale = load_prop_lines_with_cache(ODDS_API_KEY)
    except Exception:
        # Belt-and-suspenders: load_prop_lines_with_cache is written to
        # never raise, but this function runs at the top of every single
        # page load, so if some future edge case slips through anyway,
        # showing "no prop lines today" beats crashing the whole app.
        props_df = pd.DataFrame(columns=["player", "market", "point"])
        td_df = pd.DataFrame(columns=["player", "implied_prob"])
        first_td_df = pd.DataFrame(columns=["player", "implied_prob"])
        quota = empty_quota
        pulled_at = datetime.datetime.now()
        stale = False
    return props_df, td_df, first_td_df, quota, pulled_at, stale


def get_prop_lines() -> pd.DataFrame:
    df, _, _, _, _, _ = _get_prop_lines_with_timestamp()
    return df


def get_anytime_td_odds() -> pd.DataFrame:
    """player -> implied_prob (0-100): the market's implied chance a
    player scores any touchdown this week. See load_prop_lines for why
    this is kept separate from get_prop_lines()."""
    _, td_df, _, _, _, _ = _get_prop_lines_with_timestamp()
    return td_df


def get_first_td_odds() -> pd.DataFrame:
    """player -> implied_prob (0-100): the market's implied chance a
    player is specifically the FIRST player to score in their game - a
    narrower bet than get_anytime_td_odds(), and the market behind the
    First TD tab. See load_prop_lines for why this is kept separate from
    both get_prop_lines() and get_anytime_td_odds()."""
    _, _, first_td_df, _, _, _ = _get_prop_lines_with_timestamp()
    return first_td_df


def get_prop_lines_updated_at() -> datetime.datetime:
    _, _, _, _, updated_at, _ = _get_prop_lines_with_timestamp()
    return updated_at


def get_odds_api_quota() -> dict:
    """{"remaining": int|None, "used": int|None, "skipped": bool} - the
    Odds API's own usage-credit counters as of the last refresh, plus
    whether that refresh skipped pulling odds to protect the safety
    buffer (see ODDS_API_SAFETY_BUFFER in config.py)."""
    _, _, _, quota, _, _ = _get_prop_lines_with_timestamp()
    return quota


def get_prop_lines_are_stale() -> bool:
    """True if what's showing is a fallback to the last known good pull
    (because a fresh one was skipped or failed), not today's actual pull."""
    _, _, _, _, _, stale = _get_prop_lines_with_timestamp()
    return stale


@st.cache_data(ttl=3600 * 24 * 30)  # a city's coordinates never change
def get_stadium_coords(team: str):
    city = TEAM_CITY.get(team)
    if not city:
        return None
    return geocode_city(city)


@st.cache_data(ttl=3600 * 3)  # forecasts update several times a day
def get_game_weather(team: str, game_date: str):
    coords = get_stadium_coords(team)
    if not coords:
        return None
    return load_game_weather(coords[0], coords[1], game_date)


def weather_risk_pct(w: dict) -> float:
    """0-100 rough 'bad weather for football' score from wind and rain
    chance - the two conditions that most affect passing/kicking games.
    Not a forecast-confidence number, just a way to rank/color games by
    how much the weather might matter."""
    wind_pct = min(w["wind_mph"] / 25 * 100, 100)  # 25+ mph = max risk
    rain_pct = w["precip_chance"]  # already 0-100
    return max(wind_pct, rain_pct)


def severity_color(pct: float) -> str:
    """Soft green (0, calm) -> yellow -> soft red (100, severe). Same
    pastel style as matchup_rank_color but inverted, for anything where a
    HIGH number is the bad outcome (wind/rain risk) rather than a low
    one (defensive rank)."""
    t = min(max(pct, 0.0), 100.0) / 100.0
    stops = [(0.0, (143, 214, 168)), (0.5, (255, 209, 102)), (1.0, (255, 107, 107))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            local_t = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = round(c0[0] + (c1[0] - c0[0]) * local_t)
            g = round(c0[1] + (c1[1] - c0[1]) * local_t)
            b = round(c0[2] + (c1[2] - c0[2]) * local_t)
            return f"rgb({r},{g},{b})"
    return "rgb(255,107,107)"


def weather_badge(home_team: str, gameday, roof, location: str):
    """(compact weather string, risk_pct 0-100) for a game, or None if
    weather doesn't apply (indoor roof) or isn't available (too far out
    for a forecast, a neutral-site/international game where the home
    team's usual city isn't the actual venue, or the API came up empty)."""
    if pd.isna(roof) or roof in INDOOR_ROOF_STATES:
        return None
    if location != "Home":
        # Neutral-site game (e.g. an international game) - the home
        # team's usual city isn't where this one is actually played, so
        # a city-based forecast would be misleading. Skip rather than guess.
        return None
    if pd.isna(gameday):
        return None
    game_date_str = pd.Timestamp(gameday).strftime("%Y-%m-%d")
    w = get_game_weather(home_team, game_date_str)
    if not w:
        return None
    text = f"{w['temp_low']:.0f}°-{w['temp_high']:.0f}°F, wind {w['wind_mph']:.0f} mph, {w['precip_chance']:.0f}% rain"
    return text, weather_risk_pct(w)


def implied_totals(spread_line, total_line, home_team: str, away_team: str):
    """(home_implied, away_implied) points from a spread (negative =
    home favored, nflverse convention) and a game total - the number a
    prop bettor actually cares about, since a 48.5 total with a home
    team favored by 10 means a very different home/away split than the
    same total in a pick'em game. None/None if either line is missing."""
    if pd.isna(spread_line) or pd.isna(total_line):
        return None, None
    home_implied = total_line / 2 - spread_line / 2
    away_implied = total_line / 2 + spread_line / 2
    return round(home_implied, 1), round(away_implied, 1)


MIN_GAMES_FOR_CONSISTENCY = 3
# "High" consistency's coefficient-of-variation cutoff. Tightened from 0.25
# to 0.20 after a walk-forward backtest against the real 2025 season: at
# 0.25, the players it flagged only beat their own trailing average 44.1%
# of the time - statistically identical to a random player - once you
# account for weekly fantasy scoring being right-skewed (a few big games
# pull the mean up, so even a truly average player clears their own mean
# under half the time; the "50% coin flip" intuition is wrong here).
# Tightening to 0.20 was the one change, of several tested, that produced
# a real edge (50.9% vs the same 44.1% baseline - see /areas/prop-shop.md
# discussion). See the Safe Plays section for the other two changes this
# same backtest drove: dropping matchup rank as the sort key (it showed no
# edge at any threshold tested) and requiring MIN_GAMES_FOR_CONSISTENCY
# games before trusting a consistency label at all.
CONSISTENCY_HIGH_CV = 0.20


def compute_summary(player_df: pd.DataFrame, stat: str):
    """Return (avg, last_game_value, trend_vs_prior_avg, consistency_label, cv).
    Expects a single season's worth of rows already.

    consistency_label is "N/A" until MIN_GAMES_FOR_CONSISTENCY games have
    been played - with fewer, a coefficient of variation is close to
    meaningless (a single game literally always computes to a standard
    deviation of 0, which used to make a one-game player automatically
    "High" consistency - not a real signal, just an artifact of only
    having one data point). cv itself is returned alongside the label so
    callers that want to rank by consistency directly (Safe Plays) don't
    need to recompute it or reverse-engineer it from the label's bucket."""
    weeks = player_df.sort_values("week")
    if stat not in weeks.columns or weeks[stat].dropna().empty:
        return 0.0, 0.0, 0.0, "N/A", None

    values = weeks[stat].fillna(0)
    season_avg = values.mean()
    last_val = values.iloc[-1]
    trend = last_val - values.iloc[:-1].mean() if len(values) > 1 else 0.0

    std = values.std() if len(values) > 1 else 0.0
    # Coefficient of variation is only meaningful for a POSITIVE mean. A
    # negative season average (fumbles/interceptions outweighing output)
    # produces a negative cv, which used to pass "cv < CONSISTENCY_HIGH_CV"
    # and - since Safe Plays sorts by cv ascending - rank that player as the
    # single MOST consistent play on the board (caught in the Sep 2026
    # redesign QA: a -0.1 PPR player at #1). cv is None and consistency
    # "N/A" whenever the mean isn't positive.
    cv = (std / season_avg) if season_avg > 0 else None
    if cv is None or len(values) < MIN_GAMES_FOR_CONSISTENCY:
        consistency = "N/A"
    elif cv < CONSISTENCY_HIGH_CV:
        consistency = "High"
    elif cv < 0.5:
        consistency = "Medium"
    else:
        consistency = "Low"

    return season_avg, last_val, trend, consistency, cv


def sized_headshot(url: str, display_px: int) -> str:
    """Request a face-centered, properly sized crop from the NFL's
    Cloudinary-hosted headshot pipeline instead of using whatever default
    size the plain URL happens to return. Without this, photos look
    pixelated once displayed larger than ~64px, because the default
    render Cloudinary serves is fairly low-res. Requests 2x the on-page
    size for retina sharpness. Falls back to the original URL untouched
    if it doesn't match the expected NFL/Cloudinary pattern."""
    if not url or "/image/upload/" not in url:
        return url
    px = display_px * 2
    return url.replace("/image/upload/", f"/image/upload/w_{px},h_{px},c_fill,g_face,")


def delta_html(delta: float, has_prop: bool) -> str:
    if not has_prop:
        return '<span class="delta-flat">No prop line</span>'
    if delta > 0.5:
        return f'<span class="delta-up">▲ +{delta:.1f} vs line</span>'
    elif delta < -0.5:
        return f'<span class="delta-down">▼ {delta:.1f} vs line</span>'
    return '<span class="delta-flat">— even with line</span>'


def with_period_label(df: pd.DataFrame) -> pd.DataFrame:
    """Add a 'period' column like '2025 Wk3' and sort chronologically -
    needed so charts spanning multiple seasons don't jumble week numbers
    that repeat every year."""
    df = df.copy()
    df["period"] = df["season"].astype(str) + " Wk" + df["week"].astype(str)
    return df.sort_values(["season", "week"])


def chronological_order(*dfs: pd.DataFrame) -> list:
    """Given one or more period-labeled dataframes, return all their
    periods in chronological order (for pinning an Altair axis order)."""
    combined = pd.concat([d[["season", "week", "period"]] for d in dfs])
    combined = combined.drop_duplicates().sort_values(["season", "week"])
    return combined["period"].tolist()


def build_home_away_lookup(schedule: pd.DataFrame) -> pd.DataFrame:
    """season/week/team -> is_home, for every scheduled game (played or
    not yet) - covers full chart history, unlike build_next_opponent_map
    which only looks at upcoming games."""
    home_rows = schedule[["season", "week", "home_team"]].rename(columns={"home_team": "team"})
    home_rows["is_home"] = True
    away_rows = schedule[["season", "week", "away_team"]].rename(columns={"away_team": "team"})
    away_rows["is_home"] = False
    return pd.concat([home_rows, away_rows], ignore_index=True).drop_duplicates(subset=["season", "week", "team"])


def add_matchup_display(df: pd.DataFrame, home_away_lookup: pd.DataFrame) -> pd.DataFrame:
    """Add a 'matchup_display' column like '🏠 vs OPP' / '✈️ @ OPP' for
    each row, for use in chart tooltips - so hovering a point shows a
    proper game summary instead of just the bare opponent code."""
    df = df.merge(home_away_lookup, on=["season", "week", "team"], how="left")

    def _label(row):
        opponent = row.get("opponent_team")
        if pd.isna(row.get("is_home")) or pd.isna(opponent):
            return opponent if pd.notna(opponent) else "—"
        icon = HOME_ICON if row["is_home"] else AWAY_ICON
        prefix = "vs" if row["is_home"] else "@"
        return f"{icon} {prefix} {opponent}"

    df["matchup_display"] = df.apply(_label, axis=1)
    return df


# Distinct icons for home/away, used everywhere a matchup badge appears
# (cards, Deep Dive, Prop Comparator) so the two are recognizable at a
# glance, not just from the vs/@ text.
HOME_ICON = "🏠"
AWAY_ICON = "✈️"

# Badge text renders at 11px (see .matchup-badge CSS), which shrinks the
# emoji along with the rest of the text. Wrapping just the icon in a
# bigger inline span keeps it readable without enlarging the "vs OPP"
# text next to it. Only used where the label is rendered as HTML (the
# badges) - chart tooltips render matchup_display as plain text, so that
# icon stays unwrapped there.
def _icon_html(icon: str) -> str:
    return f'<span style="font-size:16px; vertical-align:-2px;">{icon}</span>'


def matchup_rank_color(rank: int, max_rank: int = 32) -> str:
    """Soft red (rank 1, toughest defense) -> soft yellow -> brand-green
    (rank max_rank, easiest defense) text color for a matchup badge.
    Colors are pastel/muted rather than pure red/green so they stay
    readable as text on the badges' dark background. The easy-matchup end
    is tinted toward theme.MATCHUP_EASY_RGB (mixed from the logo's own
    green) rather than a generic pastel green, so this semantic gradient
    still reads as on-brand even though color here means something
    (difficulty), not decoration."""
    t = (rank - 1) / max(max_rank - 1, 1)
    t = min(max(t, 0.0), 1.0)
    stops = [(0.0, theme.MATCHUP_TOUGH_RGB), (0.5, (255, 209, 102)), (1.0, theme.MATCHUP_EASY_RGB)]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            local_t = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = round(c0[0] + (c1[0] - c0[0]) * local_t)
            g = round(c0[1] + (c1[1] - c0[1]) * local_t)
            b = round(c0[2] + (c1[2] - c0[2]) * local_t)
            return f"rgb({r},{g},{b})"
    return "rgb(143,214,168)"


def pill_badge_html(label: str, color: str, tag: str = "div", title: str | None = None) -> str:
    """Shared dark-pill styling (.matchup-badge CSS) for any small colored
    label - matchup difficulty, weather risk, anytime-TD odds, etc. - so
    every gradient badge in the app looks like the same design language
    instead of each feature inventing its own. `title` becomes a native
    hover tooltip explaining what the number means - hovering on desktop
    shows it; on mobile (no hover) the badge's own label text still has
    to carry the meaning on its own, which is why every badge spells out
    a word, not just a bare number."""
    title_attr = f' title="{title}"' if title else ""
    return f'<{tag} class="matchup-badge" style="color:{color};"{title_attr}>{label}</{tag}>'


def matchup_badge_html(label: str, rank: int, tag: str = "div") -> str:
    """The full matchup-badge markup, colored by how tough the matchup
    is (matchup_rank_color). One place so cards, Deep Dive, and Prop
    Comparator all render the badge identically."""
    title = "Opponent's defensive rank this season against this stat/position: #1 = toughest, #32 = easiest."
    return pill_badge_html(label, matchup_rank_color(rank), tag, title=title)


def weather_risk_badge_html(label: str, risk_pct: float, tag: str = "div") -> str:
    """Same pill styling as matchup_badge_html, colored green (calm) to
    red (high wind/rain risk) instead of by defensive rank."""
    title = "Rough 0-100 severity score from forecasted wind speed and rain chance - higher means more likely to affect passing/kicking."
    return pill_badge_html(label, severity_color(risk_pct), tag, title=title)


def implied_total_color(total: float) -> str:
    """Soft red (weak scoring environment) -> yellow -> brand-green
    (strong scoring environment) text color, same pastel gradient style
    as matchup_rank_color. Stops span roughly the range a team's Vegas-
    implied total actually covers in a normal week (~14 to ~31)."""
    t = (total - 14.0) / (31.0 - 14.0)
    t = min(max(t, 0.0), 1.0)
    stops = [(0.0, theme.MATCHUP_TOUGH_RGB), (0.5, (255, 209, 102)), (1.0, theme.MATCHUP_EASY_RGB)]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            local_t = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = round(c0[0] + (c1[0] - c0[0]) * local_t)
            g = round(c0[1] + (c1[1] - c0[1]) * local_t)
            b = round(c0[2] + (c1[2] - c0[2]) * local_t)
            return f"rgb({r},{g},{b})"
    return "rgb(143,214,168)"


def implied_total_badge_html(total, tag: str = "div") -> str:
    """Small pill showing a team's Vegas-implied point total (see
    build_team_implied_totals) - a proxy for how good an offensive
    environment this week's game is expected to be for that team.
    Returns "" (not a badge) when the total is unknown - a bye or a game
    with no posted line yet - rather than showing a misleading number."""
    if total is None or pd.isna(total):
        return ""
    title = "This team's Vegas-implied point total for its game this week (the over/under split by the spread) - a rough gauge of how good an offensive environment this is expected to be."
    return pill_badge_html(f"📈 Implied {total:.1f} pts", implied_total_color(total), tag, title=title)


def implied_total_tier(total) -> str:
    """Bucket an implied total into high/neutral/low, the same "tiered,
    not raw-sorted" treatment confidence_tier uses for hit rate - keeps
    the resulting sort order explainable (a whole tier apart) instead of
    reshuffling on every fractional point. Missing (bye / no line yet)
    counts as neutral, never as a penalty - it's a data gap, not a signal
    that the team is a bad environment."""
    if total is None or pd.isna(total):
        return "neutral"
    if total >= 26:
        return "high"
    if total <= 19:
        return "low"
    return "neutral"


IMPLIED_TOTAL_TIER_RANK = {"high": 0, "neutral": 1, "low": 2}


def fair_prob_color(pct: float) -> str:
    """Same soft red -> yellow -> green gradient style as
    implied_total_color, but centered on a fair coin flip (50%) instead
    of a points range - a de-vigged prop's fair probability rarely
    strays far from 50% (that's what "de-vigged" means: the market's true
    view, not a book's marked-up price), so the whole visible gradient is
    compressed into a tight 40-60% band on purpose. Below 40% or above
    60% just clamps to the end color rather than needing a wider domain
    that would make ordinary values look washed-out and rare extreme
    ones indistinguishable from each other."""
    t = (pct - 40.0) / (60.0 - 40.0)
    t = min(max(t, 0.0), 1.0)
    stops = [(0.0, theme.MATCHUP_TOUGH_RGB), (0.5, (255, 209, 102)), (1.0, theme.MATCHUP_EASY_RGB)]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            local_t = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = round(c0[0] + (c1[0] - c0[0]) * local_t)
            g = round(c0[1] + (c1[1] - c0[1]) * local_t)
            b = round(c0[2] + (c1[2] - c0[2]) * local_t)
            return f"rgb({r},{g},{b})"
    return "rgb(143,214,168)"


def fair_prob_badge_html(fair_prob_over, direction: str, tag: str = "div") -> str:
    """Small pill showing the de-vigged "fair" probability of THIS pick's
    own side (Over or Under) hitting - the sportsbook market's true view
    once its margin is stripped out (see _devig_two_outcome in
    data_loader.py), as opposed to season-average-vs-line, which is our
    own model's view. The two can and do disagree - that gap is exactly
    the point of showing both: a big edge from our model that the market
    itself sees as close to a coin flip is a very different bet than one
    the market also leans toward. Purely informational (not a sort
    factor) since it measures the market's confidence, not our own.
    Returns "" when unknown - no bookmaker offered both Over and Under
    prices for this player/market, which happens for thinner markets."""
    if fair_prob_over is None or pd.isna(fair_prob_over):
        return ""
    pick_prob = fair_prob_over if direction == "▲ Over" else (1.0 - fair_prob_over)
    pct = pick_prob * 100
    title = "The sportsbook market's own de-vigged (margin stripped out) probability that this specific side hits - the market's honest view, separate from our season-average-vs-line model."
    return pill_badge_html(f"🎯 Fair {pct:.0f}%", fair_prob_color(pct), tag, title=title)


OPPORTUNITY_TREND_LAST_N = 3
OPPORTUNITY_TREND_THRESHOLD_PP = 5.0
_OPPORTUNITY_RECEIVING_STATS = {"receiving_yards", "receptions", "receiving_tds"}


def compute_opportunity_trend(stats_df: pd.DataFrame, snap_df: pd.DataFrame, player: str, stat_col: str, current_season: int, last_n: int = OPPORTUNITY_TREND_LAST_N):
    """A season average is a flat number - it can't tell you a player's
    ROLE just changed. This looks at the underlying opportunity metric
    behind a stat (not the stat itself) over the player's last `last_n`
    games this season and compares it to their own full-season average
    of that same metric, so a role that's growing or shrinking shows up
    even before enough games have passed for the stat average itself to
    catch up.

    Metric picked by what `stat_col` actually measures: a receiving stat
    (receiving_yards/receptions/receiving_tds) uses target_share (0-1,
    that player's share of the team's own targets that week - the
    standard receiving-opportunity metric, pulled straight from
    nflverse's weekly stats, see KEEP_COLUMNS). Every other stat
    (rushing, passing - and receiving stats for a player target_share
    doesn't cover, like a pure route-runner with sparse data) falls back
    to offense_pct (0-100, snap share) from load_snap_counts, which
    applies to any position and stat since it just measures "how much is
    this player even on the field."

    Returns None (not a 0/neutral result) whenever there's too little
    data to compare - fewer than 2 games played this season, or the
    metric column itself is missing/NaN for every game - since "no
    signal yet" and "confirmed stable" are different claims and this
    function only makes the second one. Returns a dict with the metric
    name, both percentages, the trend in percentage points, and a tier
    ("up"/"down"/"stable") bucketed the same "tiered, not raw-sorted"
    way every other signal on this page is, using
    OPPORTUNITY_TREND_THRESHOLD_PP as the up/down cutoff."""
    if stat_col in _OPPORTUNITY_RECEIVING_STATS:
        metric_label = "Target Share"
        pdf = stats_df[(stats_df["player"] == player) & (stats_df["season"] == current_season)].sort_values("week")
        if "target_share" not in pdf.columns:
            return None
        values = pdf["target_share"].dropna() * 100
    else:
        metric_label = "Snap Share"
        pdf = snap_df[(snap_df["player"] == player) & (snap_df["season"] == current_season)].sort_values("week")
        if "offense_pct" not in pdf.columns:
            return None
        values = pdf["offense_pct"].dropna()

    if len(values) < 2:
        return None

    season_pct = float(values.mean())
    recent_pct = float(values.tail(min(last_n, len(values))).mean())
    trend_pp = recent_pct - season_pct
    if trend_pp >= OPPORTUNITY_TREND_THRESHOLD_PP:
        tier = "up"
    elif trend_pp <= -OPPORTUNITY_TREND_THRESHOLD_PP:
        tier = "down"
    else:
        tier = "stable"
    return {"metric": metric_label, "recent_pct": recent_pct, "season_pct": season_pct, "trend_pp": trend_pp, "tier": tier, "n_games": len(values)}


def opportunity_trend_badge_html(trend: dict, tag: str = "div") -> str:
    """Small pill rendering compute_opportunity_trend's verdict. Only
    shown for "up"/"down" - a "stable" role isn't a signal worth taking
    up space for on a card that's already busy with other badges (the
    dataframe's Opportunity column still shows every tier, stable
    included, since a table row has room and a filter/sort might want
    it). Colored the same "reserved status color, not a gradient" way
    confidence_badge_html is (this is a state, not a continuous scale)."""
    if trend is None or trend["tier"] == "stable":
        return ""
    icon = "📈" if trend["tier"] == "up" else "📉"
    label = "Role trending up" if trend["tier"] == "up" else "Role trending down"
    color = theme.GOOD if trend["tier"] == "up" else theme.BAD
    title = (
        f"{trend['metric']} over this player's last {min(OPPORTUNITY_TREND_LAST_N, trend['n_games'])} games "
        f"({trend['recent_pct']:.0f}%) vs. their {CURRENT_SEASON} season average ({trend['season_pct']:.0f}%) - "
        f"a season average alone can't show a role change like this in progress."
    )
    return pill_badge_html(f"{icon} {label} ({trend['trend_pp']:+.0f}pp)", color, tag, title=title)


def opportunity_trend_text(trend: dict) -> str:
    """Plain-text rendering of compute_opportunity_trend's verdict for a
    st.dataframe cell (which shows HTML source literally rather than
    rendering it, same reasoning as every other plain-text dataframe
    column on this page) - shows every tier (including "stable"), unlike
    the badge, since a table row has room for it and it's useful to
    confirm "checked, nothing unusual" rather than just omitting it."""
    if trend is None:
        return "—"
    icon = {"up": "📈", "down": "📉", "stable": "➖"}[trend["tier"]]
    return f"{icon} {trend['metric']} {trend['trend_pp']:+.0f}pp"


def probability_color(pct: float) -> str:
    """Soft red (0%, unlikely) -> yellow -> soft green (100%, likely).
    Same pastel style and stops as matchup_rank_color, oriented so a
    HIGH number reads as green - matching how people expect a
    probability to read (unlike severity_color, where high is bad)."""
    t = min(max(pct, 0.0), 100.0) / 100.0
    stops = [(0.0, (255, 107, 107)), (0.5, (255, 209, 102)), (1.0, (143, 214, 168))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            local_t = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = round(c0[0] + (c1[0] - c0[0]) * local_t)
            g = round(c0[1] + (c1[1] - c0[1]) * local_t)
            b = round(c0[2] + (c1[2] - c0[2]) * local_t)
            return f"rgb({r},{g},{b})"
    return "rgb(143,214,168)"


def anytime_td_badge_html(pct: float, tag: str = "div") -> str:
    """'Anytime TD: NN%' badge, colored by how likely (red=unlikely,
    green=likely). The percentage is the betting market's IMPLIED
    probability - averaged across bookmakers, and it INCLUDES the book's
    built-in margin (vig), so it will always read a little higher than
    the "true" chance. It's the market's number, not a Prop Shop
    projection - spelled out here and in the on-page caption so it's
    never confused with the avg-vs-line deltas on yardage props."""
    title = (
        "Betting market's implied probability (averaged across bookmakers) that this player scores "
        "any touchdown this week. Includes the sportsbook's margin, so it runs a bit high vs. true odds. "
        "Not a Prop Shop projection."
    )
    label = f"🎯 Anytime TD: {pct:.0f}%"
    return pill_badge_html(label, probability_color(pct), tag, title=title)


def first_td_badge_html(pct: float, tag: str = "div") -> str:
    """'First TD: NN%' badge - same styling/coloring rules as
    anytime_td_badge_html, but for the much narrower "scores the FIRST
    touchdown of the game" market rather than "scores any touchdown".
    Kept as its own small badge (🥇 vs 🎯) so the two are never confused
    at a glance - a player can have a modest anytime-TD chance but still
    be the clear first-scorer favorite on his own team, or vice versa."""
    title = (
        "Betting market's implied probability (averaged across bookmakers) that this player scores "
        "the FIRST touchdown of the game - a narrower bet than Anytime TD. Includes the sportsbook's "
        "margin, so it runs a bit high vs. true odds. Not a Prop Shop projection."
    )
    label = f"🥇 First TD: {pct:.0f}%"
    return pill_badge_html(label, probability_color(pct), tag, title=title)


def _matchup_label(team: str, position: str, stat: str, next_opp_map: pd.DataFrame, defense_ranks: pd.DataFrame, plain: bool = False):
    """(text, rank) for a team's next scheduled opponent - text like
    '🏠 vs OPP — #N toughest' (home) or '✈️ @ OPP — #N toughest' (away),
    rank is that opponent's defensive rank (1=toughest, 32=easiest)
    against `stat` for `position` this season, for color-coding the
    badge. None if there's no upcoming game or no rank data for that
    stat (e.g. a bye week, or a stat with no _rank column).
    plain=True gives the icon as a bare emoji instead of the sized
    _icon_html span - use this for anywhere the result lands in plain
    text rather than being rendered via st.markdown(unsafe_allow_html=True)
    (e.g. a st.dataframe cell, which shows HTML source literally instead
    of rendering it - same reasoning as add_matchup_display's separate
    plain-text column for chart tooltips)."""
    rank_col = f"{stat}_rank"
    opp_row = next_opp_map[next_opp_map["team"] == team]
    if opp_row.empty or rank_col not in defense_ranks.columns:
        return None
    opponent = opp_row["opponent"].iloc[0]
    is_home = bool(opp_row["is_home"].iloc[0])
    kickoff = opp_row["kickoff"].iloc[0] if "kickoff" in opp_row.columns else ""
    dr = defense_ranks[(defense_ranks["position"] == position) & (defense_ranks["team"] == opponent)]
    if dr.empty:
        return None
    raw_icon = HOME_ICON if is_home else AWAY_ICON
    icon = raw_icon if plain else _icon_html(raw_icon)
    prefix = "vs" if is_home else "@"
    rank = int(dr[rank_col].iloc[0])
    kickoff_text = f" ({kickoff})" if kickoff else ""
    return f"{icon} {prefix} {opponent}{kickoff_text} — #{rank} toughest", rank


def get_matchup_label(team: str, position: str, stat: str):
    """Convenience wrapper around _matchup_label for one-off lookups
    (Player Deep Dive, Prop Comparator) - computes the opponent/defense
    maps fresh each call, which is cheap since get_schedule() and
    get_defense_ranks() are themselves cached. Tabs that loop over many
    players (Overview, Game Center) should keep using _matchup_label with
    precomputed maps instead, to avoid rebuilding them per player."""
    next_opp_map = build_next_opponent_map(get_schedule())
    defense_ranks = get_defense_ranks()
    return _matchup_label(team, position, stat, next_opp_map, defense_ranks)


def build_player_summary(view: pd.DataFrame, sort_stat: str) -> pd.DataFrame:
    """Compute per-player avg/prop-delta/consistency/matchup/injury for a
    filtered set of current-season rows. Shared by the Overview and Game
    Center tabs so both always render players the same way."""
    injuries = get_injuries()
    prop_lines = get_prop_lines()
    anytime_td = get_anytime_td_odds()
    first_td = get_first_td_odds()
    defense_ranks = get_defense_ranks()
    next_opp_map = build_next_opponent_map(get_schedule())
    prop_market = PROP_MARKET_MAP.get(sort_stat)

    summary_rows = []
    for player, pdf in view.groupby("player"):
        avg, last, trend, consistency, _cv = compute_summary(pdf, sort_stat)
        first_row = pdf.iloc[0]
        team = first_row["team"]
        position = first_row["position"]

        # Prop-line delta: season average vs. the current line for this stat
        prop_line = None
        if prop_market and not prop_lines.empty:
            match = prop_lines[(prop_lines["player"] == player) & (prop_lines["market"] == prop_market)]
            if not match.empty:
                prop_line = float(match["point"].iloc[0])
        has_prop = prop_line is not None
        delta = (avg - prop_line) if has_prop else 0.0

        # Anytime-TD odds: separate from the point-value props above -
        # a market-implied percentage, not a line to compare to an average.
        td_odds_pct = None
        if not anytime_td.empty:
            td_match = anytime_td[anytime_td["player"] == player]
            if not td_match.empty:
                td_odds_pct = float(td_match["implied_prob"].iloc[0])

        # First-TD odds: same shape as anytime-TD above, narrower market
        # (see get_first_td_odds / first_td_badge_html). Kept as its own
        # column rather than folded into td_odds_pct so a card can show
        # both badges side by side without one overwriting the other.
        first_td_pct = None
        if not first_td.empty:
            first_td_match = first_td[first_td["player"] == player]
            if not first_td_match.empty:
                first_td_pct = float(first_td_match["implied_prob"].iloc[0])

        # Matchup rank: how tough is the upcoming opponent against this position/stat?
        matchup_result = _matchup_label(team, position, sort_stat, next_opp_map, defense_ranks)
        matchup_label, matchup_rank = matchup_result if matchup_result else (None, None)

        # Injury status: only show a badge when there's an actual designation
        injury_label = None
        inj_row = injuries[injuries["player"] == player]
        if not inj_row.empty:
            status = inj_row["report_status"].iloc[0]
            if pd.notna(status):
                injury_type = inj_row["report_primary_injury"].iloc[0]
                injury_label = f"{status}" + (f" — {injury_type}" if pd.notna(injury_type) else "")

        summary_rows.append({
            "player": player,
            "position": position,
            "team": team,
            "headshot_url": first_row.get("headshot_url"),
            "team_color": first_row.get("team_color") or "#444444",
            "avg": avg,
            "last": last,
            "trend": trend,
            "consistency": consistency,
            "prop_line": prop_line,
            "has_prop": has_prop,
            "delta": delta,
            "td_odds_pct": td_odds_pct,
            "first_td_pct": first_td_pct,
            "matchup_label": matchup_label,
            "matchup_rank": matchup_rank,
            "injury_label": injury_label,
        })
    if not summary_rows:
        return pd.DataFrame()
    return pd.DataFrame(summary_rows).sort_values("avg", ascending=False)


def _summary_card_body(p, sort_stat: str) -> str:
    """Single-line-safe stat/badge block for a build_player_summary() row -
    shared by the Overview cards (render_player_cards) and every other
    card that shows a summary row (currently the Matchups tab, via
    render_hotpick_cards) so the same player is described identically no
    matter which card frame it's shown in. `p` can be a pandas Series or a
    plain dict - both support [] and .get()."""
    badges = f'<div class="consistency-badge">Consistency: {p["consistency"]}</div>'
    if pd.notna(p.get("matchup_label")):
        badges += matchup_badge_html(p["matchup_label"], int(p["matchup_rank"]))
    if pd.notna(p.get("td_odds_pct")):
        badges += anytime_td_badge_html(p["td_odds_pct"])
    if pd.notna(p.get("first_td_pct")):
        badges += first_td_badge_html(p["first_td_pct"])
    if pd.notna(p.get("injury_label")):
        badges += f'<div class="injury-badge">{p["injury_label"]}</div>'
    prop_line_text = f' (line {p["prop_line"]:.1f})' if p.get("has_prop") else ""
    return (
        f'<div class="stat-big">{p["avg"]:.1f}</div>'
        f'<div class="stat-label">avg {sort_stat.replace("_", " ")}{prop_line_text}</div>'
        f'<div style="margin-top:4px;">{delta_html(p["delta"], p.get("has_prop", False))}</div>'
        f"{badges}"
    )


def render_player_cards(summary_df: pd.DataFrame, sort_stat: str, cols_per_row: int = 4):
    """Compact player-card grid, used ONLY by the Overview tab - the one
    place on the site that deliberately keeps a denser, smaller-photo card
    instead of the large Hot-Picks-style treatment (render_hotpick_cards)
    used everywhere else, since Overview's whole job is showing a lot of
    players at once. Still reuses player_avatar_html for the photo (an
    initials-avatar fallback instead of a gap when a player has no
    headshot on file, plus the single-line-HTML construction every other
    card on the site uses) and _summary_card_body for the stats/badges, so
    the only real difference from the large cards is size and type scale."""
    for start in range(0, len(summary_df), cols_per_row):
        chunk = summary_df.iloc[start:start + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, (_, p) in zip(cols, chunk.iterrows()):
            with col:
                card_html = (
                    f'<div class="player-card" style="border-left: 4px solid {p["team_color"]};">'
                    f'<div style="display:flex; align-items:center; gap:12px;">'
                    f"{player_avatar_html(p, 132)}"
                    f"<div>"
                    f'<div style="font-weight:600;">{p["player"]}</div>'
                    f'<div style="font-size:13px; color:{theme.SUB};">{team_logo_html(p["team"])}{p["position"]} · {p["team"]}</div>'
                    f"</div>"
                    f"</div>"
                    f"{_summary_card_body(p, sort_stat)}"
                    f"</div>"
                )
                st.markdown(card_html, unsafe_allow_html=True)


def player_avatar_html(row: dict, px: int) -> str:
    """Large player photo for a card, using the same Cloudinary sizing
    pipeline (sized_headshot) as every other photo on the site. When a
    player has no headshot on file, falls back to a colored initials
    avatar (using the player's team color) at the same size, so a card
    grid never has a gap where a photo should be. A headshot that 404s at
    runtime hides itself (onerror), same fallback behavior as every other
    photo in the app (render_player_cards, First TD podium cards) -
    intentionally not swapped to the initials avatar dynamically, since
    that would mean building HTML-with-quotes inside a JS string inside
    an HTML attribute, a nesting bug waiting to happen for a case (a
    broken URL after being fetched) that's already rare."""
    url = row.get("headshot_url")
    if url and pd.notna(url):
        photo = sized_headshot(url, px)
        return f'<img src="{photo}" width="{px}" height="{px}" onerror="this.style.display=\'none\'"/>'
    name = row.get("player") or "?"
    initials = "".join(part[0] for part in name.split()[:2]).upper() or "?"
    color = row.get("team_color") or "#444444"
    font_px = max(14, px // 3)
    return (
        f'<div class="hotpick-avatar-fallback" style="width:{px}px; height:{px}px; '
        f'font-size:{font_px}px; background:{color}26; color:{color}; border:2px solid {color};">'
        f"{initials}</div>"
    )


def render_hotpick_cards(rows: list, body_fn, cols_per_row: int = 4, headshot_px: int = 128, medals: bool = False):
    """Shared large-headshot + team-logo card row for the Hot Picks page's
    three sections and its Suggested Bets panel - one component so a
    layout or styling change here applies to all four instead of four
    near-duplicate blocks (the same reasoning as render_player_cards
    above, and the same .player-card styling everywhere else on the site
    uses). `rows` is a list of plain dicts (not a DataFrame - the Hot
    Picks section already builds edge_rows/td_rows/matchup_rows/
    suggestion_rows as lists of dicts). `body_fn(row)` returns whatever
    HTML is unique to that section - a stat figure, badges - rendered
    below the player identity block. `medals=True` prefixes the first
    three cards with the same 🥇🥈🥉 podium treatment as the First TD tab."""
    # NOTE: this is built as ONE single-line string (adjacent f-string
    # literals, no actual embedded newlines) rather than a multi-line
    # triple-quoted template. A multi-line version that interpolates a
    # value which can be EMPTY (medal_html is "" whenever medals=False,
    # which is every call site except the three podium sections) leaves a
    # whitespace-only line in the markdown source. Streamlit's markdown-it
    # renderer treats that as a blank line, which terminates the raw-HTML
    # block early - everything after it (including body_fn's badges) then
    # renders as literal escaped text instead of HTML. Caught by actually
    # rendering the Suggested Bets panel (medals=False) with populated
    # data before shipping, not just the three medals=True sections, which
    # never hit this because their medal_html is never empty.
    medal_icons = ["🥇", "🥈", "🥉"]
    for start in range(0, len(rows), cols_per_row):
        chunk = rows[start:start + cols_per_row]
        cols = st.columns(cols_per_row)
        for i, (col, row) in enumerate(zip(cols, chunk)):
            rank = start + i
            with col:
                medal_html = f'<div style="font-size:20px;">{medal_icons[rank]}</div>' if medals and rank < 3 else ""
                card_html = (
                    f'<div class="player-card" style="border-left: 4px solid {row.get("team_color") or "#444444"};">'
                    f"{medal_html}"
                    f'<div style="display:flex; align-items:center; gap:14px;">'
                    f"{player_avatar_html(row, headshot_px)}"
                    f"<div>"
                    f'<div style="font-weight:700; font-size:17px;">{row["player"]}</div>'
                    f'<div style="font-size:13px; color:{theme.SUB}; margin-top:2px;">'
                    f'{team_logo_html(row["team"])}{row["position"]} · {row["team"]}</div>'
                    f"</div>"
                    f"</div>"
                    f"{body_fn(row)}"
                    f"</div>"
                )
                st.markdown(card_html, unsafe_allow_html=True)


def snapshot_hotpicks_for_tracking(edge_rows: list, td_rows: list, schedule_df: pd.DataFrame) -> None:
    """Silently save a snapshot of this week's Prop-Line Edge and TD
    Chance (Anytime + First) picks to the pick tracker, the first time
    the Hot Picks page loads after last week's snapshot - so Track
    Record can later check what actually happened. Idempotent per
    (season, week): season_week_already_tracked short-circuits every
    subsequent page load that same week, so reloading the page (or
    several people opening it) never double-counts a week's picks. Safe
    Plays isn't tracked - the user asked to track Prop Edges and TD
    Chances specifically.

    `edge_rows`/`td_rows` must be the FULL, unfiltered set (every
    position/team) - see the call site's comment for why a
    position/team-filtered session must never feed this function."""
    upcoming = schedule_df[schedule_df["home_score"].isna()]
    if upcoming.empty:
        return  # season's over (or schedule hasn't loaded) - nothing to track
    week = int(upcoming["week"].min())
    season = CURRENT_SEASON

    if pick_tracker_store.season_week_already_tracked(st.secrets, season, week):
        return

    this_week = upcoming[upcoming["week"] == week]
    team_game = {}
    for _, g in this_week.iterrows():
        kickoff = format_kickoff(g.get("gameday"), g.get("gametime"))
        team_game[g["home_team"]] = (g["game_id"], kickoff)
        team_game[g["away_team"]] = (g["game_id"], kickoff)

    new_picks = []
    for row in edge_rows:
        game = team_game.get(row["team"])
        if not game:
            continue  # team's on bye this week - nothing to resolve against
        new_picks.append({
            "id": str(uuid.uuid4()), "season": season, "week": week, "category": "edge",
            "player": row["player"], "player_id": row.get("player_id", ""),
            "team": row["team"], "position": row["position"],
            "detail": {
                "stat": row["stat"], "stat_col": row["stat_col"], "prop_line": row["prop_line"],
                "season_avg_at_pull": row["season_avg"], "direction": row["direction"],
            },
            "game_id": game[0], "kickoff": game[1], "status": "Pending", "actual": {}, "resolved_at": "",
        })
    for row in td_rows:
        game = team_game.get(row["team"])
        if not game:
            continue
        if row.get("anytime_td_pct") is not None:
            new_picks.append({
                "id": str(uuid.uuid4()), "season": season, "week": week, "category": "td_anytime",
                "player": row["player"], "player_id": row.get("player_id", ""),
                "team": row["team"], "position": row["position"],
                "detail": {"predicted_pct": row["anytime_td_pct"]},
                "game_id": game[0], "kickoff": game[1], "status": "Pending", "actual": {}, "resolved_at": "",
            })
        if row.get("first_td_pct") is not None:
            new_picks.append({
                "id": str(uuid.uuid4()), "season": season, "week": week, "category": "td_first",
                "player": row["player"], "player_id": row.get("player_id", ""),
                "team": row["team"], "position": row["position"],
                "detail": {"predicted_pct": row["first_td_pct"]},
                "game_id": game[0], "kickoff": game[1], "status": "Pending", "actual": {}, "resolved_at": "",
            })

    if new_picks:
        pick_tracker_store.add_picks(st.secrets, new_picks)


def _game_kickoff_timestamps(schedule_df: pd.DataFrame) -> dict:
    """game_id -> real pd.Timestamp kickoff moment (date + time combined),
    as opposed to a tracked pick's own "kickoff" field, which is only the
    pre-formatted DISPLAY string ("Sun 1:00 PM") snapshot_hotpicks_for_
    tracking stores - fine for showing on a card, useless for "has this
    game actually started yet" comparisons, which is what
    update_closing_lines needs. Games with no gametime posted yet are
    skipped (same "missing means unknown, not now" treatment used
    throughout schedule_logic.py)."""
    timestamps = {}
    for _, g in schedule_df.iterrows():
        gameday, gametime, game_id = g.get("gameday"), g.get("gametime"), g.get("game_id")
        if not game_id or pd.isna(gameday) or not gametime or pd.isna(gametime):
            continue
        try:
            ts = pd.Timestamp(f"{pd.Timestamp(gameday).strftime('%Y-%m-%d')} {gametime}")
        except (ValueError, TypeError):
            continue
        timestamps[game_id] = ts
    return timestamps


def update_closing_lines(picks: list, prop_lines_df: pd.DataFrame, anytime_td_df: pd.DataFrame, first_td_df: pd.DataFrame, schedule_df: pd.DataFrame) -> None:
    """Closing Line Value (CLV) capture: every time the Hot Picks page
    loads, opportunistically re-stamp each still-Pending tracked pick's
    "closing" line/probability from whatever live odds this load already
    pulled (free - no extra API cost), as long as that pick's game
    hasn't kicked off yet. Each write simply overwrites the previous one,
    so by definition the LAST write before kickoff is whatever this
    approximates as "the closing line" - there's no background scheduler
    in this app to catch the true final-seconds number, so "most recent
    line seen before kickoff" is the honest, achievable approximation,
    not a claim of catching the literal closing tick.

    CLV itself (the point of tracking this) is computed and shown on
    Track Record, not here - this function only ever writes the raw
    closing_line/closing_pct/closing_pulled_at data point into each
    pick's own `detail` dict (no schema change - `detail` is already
    free-form JSON per pick_tracker_store.py). Once a game kicks off,
    its picks are simply never touched again - whatever was captured on
    the last pre-kickoff page load stands as that pick's closing line
    for good, exactly like a real closing line would.

    Deliberately NOT fed back into live Hot Picks ranking (see the
    Section 1/2 sort comments) - CLV is inherently retrospective
    (comparing where a line ended up to where it opened), so it can only
    ever describe a pick already made, never help rank a new one."""
    pending = [p for p in picks if p.get("status") == "Pending"]
    if not pending:
        return

    kickoffs = _game_kickoff_timestamps(schedule_df)
    now = pd.Timestamp.now()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    updated = []
    for pick in pending:
        kickoff_ts = kickoffs.get(pick.get("game_id"))
        # No posted kickoff time yet, or it's already passed - either way
        # there's nothing safe to call a *closing* line right now (an
        # in-progress or unscheduled game has no meaningful "close").
        if kickoff_ts is None or now >= kickoff_ts:
            continue

        detail = dict(pick.get("detail") or {})
        category = pick.get("category")
        player = pick.get("player")
        changed = False

        if category == "edge" and not prop_lines_df.empty:
            stat_col = detail.get("stat_col")
            market = PROP_MARKET_MAP.get(stat_col) if stat_col else None
            if market:
                match = prop_lines_df[(prop_lines_df["player"] == player) & (prop_lines_df["market"] == market)]
                if not match.empty:
                    detail["closing_line"] = round(float(match["point"].iloc[0]), 1)
                    changed = True
        elif category in ("td_anytime", "td_first"):
            source = anytime_td_df if category == "td_anytime" else first_td_df
            if not source.empty:
                match = source[source["player"] == player]
                if not match.empty:
                    detail["closing_pct"] = round(float(match["implied_prob"].iloc[0]), 1)
                    changed = True

        if changed:
            detail["closing_pulled_at"] = now_iso
            pick = dict(pick)
            pick["detail"] = detail
            updated.append(pick)

    if updated:
        pick_tracker_store.update_picks(st.secrets, updated)


def resolve_pending_picks() -> tuple:
    """Check every still-Pending tracked pick whose game has gone final
    and mark it Hit/Miss/Push based on what actually happened. Returns
    (newly_resolved_count, still_pending_count). Safe to call as often as
    wanted - a pick whose game isn't final yet is simply left Pending.

    Hit/miss rules:
      - edge: the player's actual value for that stat this week vs. the
        prop line, on the predicted side (Over/Under). Equal to the line
        is a Push.
      - td_anytime: hit if the player had any rushing + receiving TD that
        week (the same definition the First TD tab itself uses for
        "season TDs" - rushing_tds + receiving_tds - so a pick's result
        here always agrees with what the rest of the site would say).
      - td_first: hit if the player is that game's first touchdown scorer
        per play-by-play (get_first_td_scorers) - matched by player_id,
        not name, since pbp's td_player_name is an abbreviated "J.Love"
        style that doesn't reliably match this app's full display names.
        A game with no recorded touchdowns (or not yet in the pbp pull)
        makes every td_first pick for it a Miss once the game is final -
        see load_first_td_scorers's docstring for the one simplification
        this carries (doesn't special-case a defensive/special-teams
        score the way a real sportsbook market would)."""
    picks = pick_tracker_store.load_picks(st.secrets)
    pending = [p for p in picks if p["status"] == "Pending"]
    if not pending:
        return 0, 0

    schedule_df = get_schedule()
    final_games = set(schedule_df[schedule_df["home_score"].notna()]["game_id"])
    resolvable = [p for p in pending if p.get("game_id") in final_games]
    if not resolvable:
        return 0, len(pending)

    stats_df = get_stats()
    stats_df = stats_df[stats_df["season"] == CURRENT_SEASON]
    first_td_df = get_first_td_scorers()
    first_td_by_game = first_td_df.set_index("game_id")["first_td_player_id"].to_dict() if not first_td_df.empty else {}

    updated = []
    for pick in resolvable:
        actual_row = stats_df[
            (stats_df["player_id"] == pick["player_id"]) & (stats_df["week"] == pick["week"])
        ] if pick.get("player_id") else pd.DataFrame()

        if pick["category"] == "edge":
            if actual_row.empty or pick["detail"]["stat_col"] not in actual_row.columns:
                status, actual = "Miss", {"note": "No stat line found for this player/week"}
            else:
                actual_val = float(actual_row[pick["detail"]["stat_col"]].iloc[0])
                line = pick["detail"]["prop_line"]
                if actual_val == line:
                    status = "Push"
                elif pick["detail"]["direction"] == "▲ Over":
                    status = "Hit" if actual_val > line else "Miss"
                else:
                    status = "Hit" if actual_val < line else "Miss"
                actual = {"actual_value": actual_val}

        elif pick["category"] == "td_anytime":
            if actual_row.empty:
                status, actual = "Miss", {"actual_tds": 0}
            else:
                tds = int(actual_row.get("rushing_tds", pd.Series([0])).fillna(0).iloc[0]) + \
                      int(actual_row.get("receiving_tds", pd.Series([0])).fillna(0).iloc[0])
                status = "Hit" if tds > 0 else "Miss"
                actual = {"actual_tds": tds}

        else:  # td_first
            scorer_id = first_td_by_game.get(pick.get("game_id"))
            status = "Hit" if scorer_id and scorer_id == pick.get("player_id") else "Miss"
            actual = {"first_td_scorer_id": scorer_id or ""}

        pick["status"] = status
        pick["actual"] = actual
        pick["resolved_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        updated.append(pick)

    if updated:
        pick_tracker_store.update_picks(st.secrets, updated)
    return len(updated), len(pending) - len(updated)


def compute_hit_rate(resolved_picks: list, category: str = None):
    """Hit rate (0-100) over a list of already-resolved (non-Pending)
    picks, optionally narrowed to one pick_tracker_store category -
    Pushes are excluded from the denominator (a Push is neither a hit nor
    a miss), same convention a sportsbook uses for its own record. Shared
    by the Track Record tab and the Home page's KPI tile so "hit rate"
    means exactly the same thing in both places. Returns (pct_or_None,
    hits, scored_count) - pct is None when there's nothing scored yet."""
    scored = [
        p for p in resolved_picks
        if p["status"] in ("Hit", "Miss") and (category is None or p["category"] == category)
    ]
    if not scored:
        return None, 0, 0
    hits = sum(1 for p in scored if p["status"] == "Hit")
    return hits / len(scored) * 100, hits, len(scored)


def compute_clv(pick: dict):
    """Closing Line Value for one tracked pick: how far the line moved in
    THIS pick's favor between when it was made (the opening line/
    probability, captured at snapshot time) and the last value seen
    before kickoff (the "closing" line/probability, opportunistically
    captured by update_closing_lines - see its docstring for why that's
    an honest approximation of a true closing line, not the literal
    final-seconds number). Returns None whenever a closing value hasn't
    been captured yet (the game hasn't reached kickoff on any page load
    since this pick was made) - CLV is undefined until then, not zero.

    CLV is deliberately independent of whether the pick actually hit -
    it measures whether the market itself came around to agree with the
    call, which professional sports-betting analytics treats as the more
    reliable long-run skill signal (a lot of noise decides any single
    game's outcome; a market move is thousands of people's money voting).

    - "edge" picks: positive means the line moved toward the picked
      side (e.g. picked Over 75.5, closing line is 78.5 -> +3.0, the
      market now thinks MORE yards are likely, same direction as the
      pick). Units are the stat's own points (yards, receptions, etc.).
    - "td_anytime" / "td_first" picks: positive means the market's
      implied probability of the SAME event (this player scoring) went
      up since the pick was made. Units are percentage points."""
    detail = pick.get("detail", {})
    if pick.get("category") == "edge":
        opening = detail.get("prop_line")
        closing = detail.get("closing_line")
        if opening is None or closing is None:
            return None
        diff = closing - opening
        return diff if detail.get("direction") == "▲ Over" else -diff
    elif pick.get("category") in ("td_anytime", "td_first"):
        opening = detail.get("predicted_pct")
        closing = detail.get("closing_pct")
        if opening is None or closing is None:
            return None
        return closing - opening
    return None


def compute_clv_summary(picks: list) -> dict:
    """category -> {"avg_clv": float, "n": int} across every pick with a
    captured CLV value (see compute_clv) - Pending picks are included as
    long as a closing line was captured, not just resolved ones, since
    CLV describes the market's move and doesn't depend on the outcome
    being known yet. A category with zero CLV-eligible picks is simply
    absent from the returned dict."""
    buckets: dict = {}
    for p in picks:
        clv = compute_clv(p)
        if clv is None:
            continue
        buckets.setdefault(p["category"], []).append(clv)
    return {cat: {"avg_clv": sum(vals) / len(vals), "n": len(vals)} for cat, vals in buckets.items()}


def count_resolvable_picks(picks: list, schedule_df: pd.DataFrame) -> int:
    """How many still-Pending picks have a game that's already final -
    i.e. how many "🔄 Check results now" on Track Record would actually
    resolve right now. Read-only mirror of resolve_pending_picks's own
    pending/final-games logic, used just for the Home page's alert nudge
    so it doesn't need to actually mutate anything to know there's
    something worth checking."""
    final_games = set(schedule_df[schedule_df["home_score"].notna()]["game_id"])
    return sum(1 for p in picks if p["status"] == "Pending" and p.get("game_id") in final_games)


# ---- Continuous-improvement loop: Hot Picks re-reads its own track record
# every page load and lets it influence how it ranks and badges its own
# suggestions - see compute_segment_confidence's docstring for the full
# mechanics. Tunable in one place:
CONFIDENCE_MIN_N = 5      # a (category, position) segment needs at least this
                           # many resolved picks before its hit rate is trusted
                           # for anything - below this it's shown as "new" and
                           # never boosts or penalizes a pick's ranking.
CONFIDENCE_HIGH_PCT = 60.0  # segment hit rate at/above this = "hot" (🔥)
CONFIDENCE_LOW_PCT = 40.0   # segment hit rate at/below this = "cold" (🧊)
CONFIDENCE_TIER_RANK = {"high": 0, "neutral": 1, "new": 2, "low": 3}


def confidence_tier(pct: float, n: int, min_n: int = CONFIDENCE_MIN_N) -> str:
    """"high" / "neutral" / "low" / "new" from a segment's raw hit rate -
    "new" (not enough resolved picks yet to trust the number) always wins
    regardless of what the percentage happens to be, which is what keeps
    an early 2-for-2 from being treated the same as a real 12-for-20."""
    if n < min_n:
        return "new"
    if pct >= CONFIDENCE_HIGH_PCT:
        return "high"
    if pct <= CONFIDENCE_LOW_PCT:
        return "low"
    return "neutral"


def compute_segment_confidence(picks: list, min_n: int = CONFIDENCE_MIN_N) -> dict:
    """The read half of Hot Picks' continuous-improvement loop: groups
    every RESOLVED (Hit/Miss only - Push and Pending excluded, same
    convention as compute_hit_rate) tracked pick by (category, position)
    - e.g. ("edge", "WR") or ("td_anytime", "RB") - and returns each
    segment's hit rate, sample size, and confidence tier. Hot Picks reads
    this back to badge and re-sort its own suggestions
    (confidence_badge_html renders it, the Section 1/2 sort keys use the
    tier), so a segment that's actually been missing sinks toward the
    bottom of its list and one that's been hitting rises - without ever
    hiding a pick outright. Safe Plays has no entry here on purpose (that
    category isn't tracked in Track Record at all - "high consistency"
    has no single hit/miss to score).

    Returns {(category, position): {"pct": float, "n": int, "tier": str}}
    - a missing key means zero resolved picks for that segment yet, which
    every caller treats identically to an explicit "new" tier."""
    buckets: dict = {}
    for p in picks:
        if p["status"] not in ("Hit", "Miss"):
            continue
        buckets.setdefault((p["category"], p.get("position") or ""), []).append(p["status"] == "Hit")
    segments = {}
    for key, hits in buckets.items():
        n = len(hits)
        pct = sum(hits) / n * 100
        segments[key] = {"pct": pct, "n": n, "tier": confidence_tier(pct, n, min_n)}
    return segments


def confidence_badge_html(segment, min_n: int = CONFIDENCE_MIN_N) -> str:
    """Small badge rendering compute_segment_confidence's verdict on one
    pick's (category, position) segment - the visible half of the
    continuous-improvement loop. `segment` is that dict's per-key value,
    or None when the segment has no resolved picks at all yet (same
    treatment as an explicit "new" tier)."""
    if segment is None:
        return (
            f'<div class="confidence-badge confidence-new">🆕 New segment '
            f'<span style="text-transform:none;">— needs {min_n}+ resolved picks</span></div>'
        )
    tier, pct, n = segment["tier"], segment["pct"], segment["n"]
    if tier == "new":
        return (
            f'<div class="confidence-badge confidence-new">🆕 {pct:.0f}% so far '
            f'<span style="text-transform:none;">({n}/{min_n}+ picks — not enough yet)</span></div>'
        )
    icon = {"high": "🔥", "neutral": "➖", "low": "🧊"}[tier]
    label = {"high": "Hot segment", "neutral": "Even segment", "low": "Cold segment"}[tier]
    return f'<div class="confidence-badge confidence-{tier}">{icon} {label} — {pct:.0f}% hit rate ({n} picks)</div>'


def _confidence_label_text(segment) -> str:
    """Plain-text (non-HTML) equivalent of confidence_badge_html, for the
    st.dataframe "Confidence" columns - a dataframe cell shows HTML source
    literally rather than rendering it, same reasoning as every other
    plain=True/plain-text column elsewhere in this file."""
    if segment is None:
        return "🆕 New"
    tier, pct, n = segment["tier"], segment["pct"], segment["n"]
    if tier == "new":
        return f"🆕 {pct:.0f}% ({n} picks)"
    icon = {"high": "🔥", "neutral": "➖", "low": "🧊"}[tier]
    return f"{icon} {pct:.0f}% ({n} picks)"


# ---- Utility toolbar: one row directly under the wordmark ----
# (Sep 2026 redesign) These four controls used to stack down the left
# edge as separate rows. Same controls, same behavior - just one row:
# info popovers on the left, status message in the middle, refresh
# actions on the right. Deliberately uses no st.columns() arguments newer
# than what the rest of this file already relies on, so it can't break on
# an older Streamlit Cloud runtime.
tb_season, tb_status, tb_msg, tb_refresh_all, tb_refresh_props = st.columns([1.15, 1.45, 3.1, 1.45, 1.85])
with tb_season:
    theme.info_popover(
        f"**Current season:** {CURRENT_SEASON}. Trend charts include prior seasons' data for longer-term context.",
        label="ℹ️ Season info", use_container_width=True,
    )

if not ODDS_API_KEY:
    with tb_msg:
        st.info("No prop odds API key configured yet - card deltas will show \"No prop line\" until one is added. See README for setup.", icon="ℹ️")
else:
    prop_updated_at = get_prop_lines_updated_at()
    quota = get_odds_api_quota()
    is_stale = get_prop_lines_are_stale()
    quota_text = (
        f"🔑 Odds API quota: {quota['remaining']:,} credits remaining ({quota['used']:,} used this billing period)"
        if quota["remaining"] is not None else "Odds API quota info not available."
    )
    with tb_status:
        theme.info_popover(
            f"**Prop lines last pulled:** {prop_updated_at.strftime('%a %-I:%M %p')} (auto-refreshes once a day "
            f"to conserve API quota).\n\n{quota_text}",
            label="🔑 Prop line status", use_container_width=True,
        )
    with tb_msg:
        if is_stale:
            st.warning(
                f"Couldn't get a fresh pull this cycle (quota safety buffer or a temporary API hiccup) - showing the last "
                f"known odds from {prop_updated_at.strftime('%a %-I:%M %p')} instead of nothing. Will try again next refresh.",
                icon="⚠️",
            )
    with tb_refresh_props:
        if st.button("🔄 Refresh prop lines now", use_container_width=True):
            _get_prop_lines_with_timestamp.clear()
            st.rerun()

with tb_refresh_all:
    if st.button("Refresh all data now", use_container_width=True):
        # Clearing st.cache_data alone isn't enough - nflreadpy (the library
        # that actually downloads rosters/stats/schedules from nflverse) keeps
        # its own separate cache underneath this one, so without also clearing
        # that, this button could still silently serve up to an hour-old data
        # (see clear_nflverse_cache's docstring in data_loader.py). Both need
        # to be cleared together for "refresh now" to actually mean "now".
        clear_nflverse_cache()
        st.cache_data.clear()
        st.rerun()

stats_df = get_stats()
meta_df = get_meta()

if stats_df.empty:
    st.warning("No data available yet.")
    st.stop()

merged = stats_df.merge(meta_df, on="player", how="left")
current_season_df = merged[merged["season"] == CURRENT_SEASON]

team_logos = get_team_meta().set_index("team_abbr")["team_logo_espn"]


def team_logo_html(team, px: int = 30) -> str:
    """Small inline team logo, or an empty string if the team's unknown
    or has no logo on file - used wherever a team abbreviation is shown
    (cards, Deep Dive, Game Center, Prop Comparator) so a team is a quick
    visual identifier everywhere, not just in the Matchups table."""
    url = team_logos.get(team) if pd.notna(team) else None
    if not url or pd.isna(url):
        return ""
    return (
        f'<img src="{url}" width="{px}" height="{px}" '
        f'style="vertical-align:middle; margin-right:6px; border-radius:4px;" '
        f'onerror="this.style.display=\'none\'"/>'
    )


def render_rosters_tab():
    """Create/edit/select up to roster_store.MAX_ROSTERS named rosters -
    just storage and management for now (see roster_store.py for why this
    needs Google Sheets rather than local disk). The lineup optimizer that
    actually uses a selected roster to build a start/sit recommendation is
    a separate, later feature - this tab only manages who's ON a roster."""
    if roster_store.using_local_fallback(st.secrets):
        reason = roster_store.last_connection_error()
        detail = f"\n\n**Reason:** `{reason}`" if reason else ""
        st.warning(
            "Google Sheets isn't configured yet, so rosters are being saved to this app's local disk instead - "
            "that storage does NOT survive the next code deploy. See README to set up the Sheets connection "
            "before relying on this for real. Local saves work fine for now, just don't build a whole roster "
            "you'd be upset to lose." + detail,
            icon="⚠️",
        )

    rosters = roster_store.load_rosters(st.secrets)
    all_players = sorted(current_season_df["player"].dropna().unique())

    roster_names = {r["id"]: r["name"] for r in rosters}
    pick_options = ["+ New roster"] + list(roster_names.keys())
    picked_id = st.selectbox(
        "Roster", pick_options,
        format_func=lambda rid: "+ New roster" if rid == "+ New roster" else roster_names[rid],
        key="roster_picker",
    )

    editing = next((r for r in rosters if r["id"] == picked_id), None)
    default_name = editing["name"] if editing else ""
    default_players = editing["players"] if editing else []
    default_settings = (editing.get("lineup_settings") if editing else None) or roster_store.DEFAULT_LINEUP_SETTINGS

    with st.form("roster_form"):
        name = st.text_input("Roster name", value=default_name, max_chars=40, placeholder="e.g. Curtis's Team")
        players = st.multiselect(
            "Players on this roster", all_players, default=[p for p in default_players if p in all_players],
            help="Only players TPS is currently tracking (this season's depth-chart starters) show up here.",
        )
        missing = [p for p in default_players if p not in all_players]
        if missing:
            st.caption(f"Not shown (no longer tracked this season): {', '.join(missing)}")

        st.markdown("**League starting lineup**")
        st.caption("How many of each slot your league starts - the Lineup Optimizer fills exactly this many.")
        settings_cols = st.columns(5)
        slot_labels = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "FLEX": "FLEX (RB/WR/TE)"}
        lineup_inputs = {}
        for col, slot in zip(settings_cols, roster_store.DEFAULT_LINEUP_SETTINGS):
            with col:
                lineup_inputs[slot] = st.number_input(
                    slot_labels[slot], min_value=0, max_value=6,
                    value=default_settings.get(slot, roster_store.DEFAULT_LINEUP_SETTINGS[slot]),
                    step=1, key=f"lineup_setting_{slot}",
                )

        save_col, delete_col = st.columns([3, 1])
        with save_col:
            submitted = st.form_submit_button("💾 Save roster", use_container_width=True, type="primary")
        with delete_col:
            delete_clicked = st.form_submit_button(
                "🗑️ Delete", use_container_width=True, disabled=(editing is None),
            )

    if submitted:
        if not name.strip():
            st.error("Give the roster a name before saving.")
        elif not players:
            st.error("Add at least one player before saving.")
        elif sum(lineup_inputs.values()) == 0:
            st.error("Set at least one starting lineup slot above 0.")
        else:
            roster_id = picked_id if editing else str(uuid.uuid4())
            ok, msg = roster_store.save_roster(st.secrets, roster_id, name.strip(), players, lineup_inputs)
            # Not resetting the "Roster" dropdown's selection here on
            # purpose - it's already been instantiated earlier in this
            # same run, and Streamlit raises StreamlitWidgetAlreadyInstantiatedError
            # if you assign to a widget's session_state key after that
            # point (same rule this codebase already follows elsewhere,
            # e.g. jump_to_game_center). The saved roster shows up in the
            # list below immediately either way.
            (st.success if ok else st.error)(msg)

    if delete_clicked and editing:
        roster_store.delete_roster(st.secrets, editing["id"])
        st.success(f"Deleted \"{editing['name']}\".")

    if rosters:
        st.divider()
        st.caption(f"{len(rosters)}/{roster_store.MAX_ROSTERS} rosters saved")
        for r in rosters:
            settings = r.get("lineup_settings") or roster_store.DEFAULT_LINEUP_SETTINGS
            settings_summary = " / ".join(f"{n} {slot}" for slot, n in settings.items() if n > 0)
            with st.expander(f"{r['name']} — {len(r['players'])} players"):
                st.caption(f"Starting lineup: {settings_summary}")
                st.write(", ".join(r["players"]) if r["players"] else "No players yet.")


# Slot counts are per-roster now (leagues vary - some run 1 FLEX, some 2,
# some more WRs, etc.), stored alongside each roster via roster_store and
# editable on the My Rosters tab; roster_store.DEFAULT_LINEUP_SETTINGS is
# what a roster gets until someone changes it. No DEF/K slots since TPS
# doesn't track defense/kicker stats, so those aren't configurable here.
# FLEX_ELIGIBLE, SLOT_ORDER, build_lineup_slots, matchup_adjustment, and
# teams_playing_this_week now live in schedule_logic.py (imported above)
# so they can be unit tested without importing this whole Streamlit script.


def compute_lineup_projections(roster_players: list) -> pd.DataFrame:
    """One row per roster player: season avg PPR points, next opponent,
    matchup-adjusted projection for this week, and injury status. Players
    with no current-season data (not tracked, or not enough games) are
    still included with proj = 0.0. A player whose team has a bye THIS
    week gets proj = 0.0 and note = "Bye this week" - even though they do
    have a next scheduled opponent (just not until next week), which
    would otherwise make them look startable when they can't play at
    all right now."""
    defense_ranks = get_defense_ranks()
    schedule = get_schedule()
    next_opp_map = build_next_opponent_map(schedule)
    teams_this_week = teams_playing_this_week(schedule)
    injuries = get_injuries()

    rows = []
    for player in roster_players:
        pdf = current_season_df[current_season_df["player"] == player]
        if pdf.empty:
            rows.append({
                "player": player, "position": "?", "team": "?", "opponent": None, "opponent_plain": None,
                "season_avg": 0.0, "matchup_rank": None, "proj_points": 0.0,
                "injury_status": None, "note": "Not tracked this season", "headshot_url": None,
            })
            continue
        latest = pdf.sort_values(["season", "week"]).iloc[-1]
        position, team = latest["position"], latest["team"]
        headshot_url = latest.get("headshot_url")
        season_avg, _, _, _, _ = compute_summary(pdf[pdf["season"] == CURRENT_SEASON], "fantasy_points_ppr")

        if team not in teams_this_week:
            rows.append({
                "player": player, "position": position, "team": team, "opponent": None, "opponent_plain": None,
                "season_avg": round(season_avg, 1), "matchup_rank": None, "proj_points": 0.0,
                "injury_status": "Bye", "note": "Bye this week - not eligible to start", "headshot_url": headshot_url,
            })
            continue

        matchup = _matchup_label(team, position, "fantasy_points_ppr", next_opp_map, defense_ranks)
        # Separate plain-text opponent label for the "Full roster
        # comparison" st.dataframe below - st.dataframe shows HTML source
        # literally instead of rendering it, so the badge-flavored HTML
        # label (used by lineup_row's on-brand badge) would otherwise show
        # up as raw <span> tags in that table.
        matchup_plain = _matchup_label(team, position, "fantasy_points_ppr", next_opp_map, defense_ranks, plain=True)
        rank = matchup[1] if matchup else None
        note = "" if matchup else "No matchup data found"
        proj = round(season_avg * matchup_adjustment(rank), 1)

        inj_row = injuries[injuries["player"] == player] if not injuries.empty else pd.DataFrame()
        inj_status = inj_row["report_status"].iloc[0] if not inj_row.empty else None

        rows.append({
            "player": player, "position": position, "team": team,
            "opponent": matchup[0] if matchup else None,
            "opponent_plain": matchup_plain[0] if matchup_plain else None,
            "season_avg": round(season_avg, 1), "matchup_rank": rank, "proj_points": proj,
            "injury_status": inj_status, "note": note, "headshot_url": headshot_url,
        })
    return pd.DataFrame(rows)


def render_lineup_tab():
    """Pick a saved roster, see the matchup-adjusted optimal starting
    lineup, and compare every rostered player side by side. Read-only
    output built on top of My Rosters - it doesn't save anything itself."""
    rosters = roster_store.load_rosters(st.secrets)
    if not rosters:
        st.info("No saved rosters yet - add one on the **My Rosters** tab first.")
        return

    roster_names = {r["id"]: r["name"] for r in rosters}
    picked_id = st.selectbox(
        "Roster", list(roster_names.keys()), format_func=lambda rid: roster_names[rid], key="lineup_roster_picker",
    )
    roster = next(r for r in rosters if r["id"] == picked_id)
    lineup_settings = roster.get("lineup_settings") or roster_store.DEFAULT_LINEUP_SETTINGS
    lineup_slots = build_lineup_slots(lineup_settings)

    proj_df = compute_lineup_projections(roster["players"])
    lineup, bench, unfillable = optimize_lineup(proj_df, lineup_slots)

    settings_summary = " / ".join(f"{n} {slot}" for slot, n in lineup_settings.items() if n > 0)
    cap_col, info_col = st.columns([5, 1])
    with cap_col:
        st.caption(f"League settings for this roster: {settings_summary} (edit on the **My Rosters** tab).")
    with info_col:
        theme.info_popover(
            "**How projections are calculated:** this season's average PPR points per game, adjusted ±15% by "
            "the upcoming opponent's defensive rank against that position (see the Matchups tab for how that "
            "rank is computed). This is a simple, transparent estimate, not a black-box model - use it as a "
            "starting point, not gospel.",
            label="ℹ️ How this works",
        )
    if unfillable:
        from collections import Counter
        counts = Counter(unfillable)
        parts = [f"{slot}" + (f" ×{n}" if n > 1 else "") for slot, n in counts.items()]
        st.warning(f"Couldn't fill every slot from this roster - missing eligible players at: {', '.join(parts)}.", icon="⚠️")

    total_proj = sum(p["proj_points"] for p in lineup.values() if p is not None)
    st.metric("Optimal lineup — projected total", f"{total_proj:.1f} pts")

    def lineup_row(slot_label, row):
        if row is None:
            st.markdown(f"**{slot_label}** — *no eligible player*")
            return
        badge = ""
        # Same NaN-vs-None trap as below: a bye-week player's matchup_rank
        # comes back as NaN (not None) once it's passed through a
        # DataFrame, and "is not None" doesn't catch that - int(nan)
        # raises ValueError, which would crash this tab on any bye week.
        if pd.notna(row["matchup_rank"]):
            badge = matchup_badge_html(f"{row['opponent']}", int(row["matchup_rank"]), tag="span")
        # pd.notna, not truthiness: a None injury_status becomes NaN once
        # these rows pass through a DataFrame, and bool(float('nan')) is
        # True in Python - a plain "if row['injury_status']" check would
        # print the literal word "nan" for every healthy player.
        inj = f" · {row['injury_status']}" if pd.notna(row["injury_status"]) else ""

        # Large Hot-Picks-style card (player_avatar_html gives the same
        # 128px photo-or-initials-avatar treatment as every other large
        # card on the site) and single-line HTML - a lineup is at most a
        # handful of slots, never the "many players" case Overview exists
        # for, so this always gets the large card.
        card_html = (
            f'<div class="player-card" style="display:flex; align-items:center; gap:16px;">'
            f"{player_avatar_html(row, 128)}"
            f"<div>"
            f'<div style="font-size:13px; color:{theme.SUB}; text-transform:uppercase; letter-spacing:.03em;">{slot_label}</div>'
            f'<div style="font-size:19px; font-weight:700;">{row["player"]} <span style="font-weight:400; color:{theme.SUB};">({team_logo_html(row["team"])}{row["team"]})</span></div>'
            f'<div style="margin-top:4px;">{badge}</div>'
            f'<div style="margin-top:4px; color:{theme.SUB};">Proj <b style="color:{theme.INK};">{row["proj_points"]}</b> pts · season avg {row["season_avg"]}{inj}</div>'
            f"</div>"
            f"</div>"
        )
        st.markdown(card_html, unsafe_allow_html=True)

    # Number duplicate slot labels (RB 1/RB 2, FLEX 1/FLEX 2, etc.) so two
    # identical-looking rows aren't both just labeled "RB" with no way to
    # tell them apart - applies consistently to every slot that repeats.
    slot_seen = {}
    for i, slot in enumerate(lineup_slots):
        key = f"{slot}_{i}"
        slot_seen[slot] = slot_seen.get(slot, 0) + 1
        label = f"{slot} {slot_seen[slot]}" if lineup_slots.count(slot) > 1 else slot
        lineup_row(label, lineup.get(key))

    st.divider()
    st.subheader("Full roster comparison")
    st.caption("Everyone on this roster, ranked by projection - includes bench players and anyone excluded from the lineup above.")
    display = proj_df.sort_values("proj_points", ascending=False).copy()
    display["in_lineup"] = display["player"].isin(
        {r["player"] for r in lineup.values() if r is not None}
    ).map({True: "✅", False: ""})
    # Blank out missing values instead of showing the literal word "None" -
    # a healthy player has no injury_status, an unranked matchup has no
    # note, and st.dataframe shows Python's None/NaN as visible text
    # rather than leaving the cell empty like st.markdown would.
    display[["opponent_plain", "matchup_rank", "injury_status", "note"]] = (
        display[["opponent_plain", "matchup_rank", "injury_status", "note"]].fillna("")
    )
    st.dataframe(
        display[["in_lineup", "player", "position", "team", "opponent_plain", "proj_points", "season_avg", "matchup_rank", "injury_status", "note"]]
        .rename(columns={
            "in_lineup": "Start", "player": "Player", "position": "Pos", "team": "Team",
            "opponent_plain": "Next opp", "proj_points": "Proj", "season_avg": "Szn avg",
            "matchup_rank": "Opp rank", "injury_status": "Injury", "note": "Note",
        }),
        width="content", hide_index=True, row_height=38,
        column_config={
            "Start": st.column_config.TextColumn(width="small"),
            "Player": st.column_config.TextColumn(width=200),
            "Pos": st.column_config.TextColumn(width="small"),
            "Team": st.column_config.TextColumn(width="small"),
            "Next opp": st.column_config.TextColumn(width=270),
            "Proj": st.column_config.NumberColumn(width="small"),
            "Szn avg": st.column_config.NumberColumn(width="small"),
            "Opp rank": st.column_config.NumberColumn(width="small"),
            "Injury": st.column_config.TextColumn(width=140),
            "Note": st.column_config.TextColumn(width=200),
        },
    )


SITE_SECTIONS = ["🏠 Dashboard", "🔍 Research", "🏈 Lineups", "🎯 Props", "🔥 Hot Picks", "📊 Track Record"]
# A browser session that was open before the Sep 2026 nav redesign can
# still hold an old section name ("🏈 Fantasy Lineups", "🎯 Prop Bets",
# "🏠 Home") in session state - drop it so the radio falls back to its
# default instead of erroring on a value that's no longer an option.
if st.session_state.get("site_side") not in (None, *SITE_SECTIONS):
    del st.session_state["site_side"]
tab_side = st.radio(
    "Site section", SITE_SECTIONS,
    horizontal=True, key="site_side", label_visibility="collapsed",
)
# (No st.divider() here - the nav bar's own full-width bottom rule, styled
# in theme.py, is the separator. Both together read as a doubled line.)

# Site IA (Sep 2026 redesign): the old "Fantasy Lineups" section held six
# sub-tabs covering two different jobs - browsing stats and managing your
# own lineups. It's split into Research (browse/scout) and Lineups (build/
# optimize). Each sub-tab was already a self-contained `with` block with no
# shared setup between them, so the split moves no logic - only which
# top-level section each one lives under. "Prop Bets" is renamed "Props"
# with its four sub-tabs unchanged.
if tab_side == "🔍 Research":
    tab_overview, tab_deep_dive, tab_injuries, tab_matchups = st.tabs(
        ["📋 Overview", "🔍 Player Deep Dive", "🩹 Injuries", "🗓️ Matchups"]
    )
elif tab_side == "🏈 Lineups":
    tab_rosters, tab_lineup = st.tabs(
        ["👥 My Rosters", "🏆 Lineup Optimizer"]
    )
elif tab_side == "🎯 Props":
    tab_props, tab_firsttd, tab_game, tab_slips = st.tabs(
        ["🎯 Prop Comparator", "🥇 First TD", "🏟️ Game Center", "🧾 Bet Slip Tracker"]
    )
# 🏠 Dashboard, 🔥 Hot Picks and 📊 Track Record have no sub-tabs of their own -
# each is one combined page, rendered further down in its own
# `elif tab_side == "...":` branch.

if st.session_state.pop("show_jump_toast", False):
    # Set by the Matchups tab's "Open in Game Center" button (see
    # jump_to_game_center below). Shown up here, once, right after the
    # rerun it triggers - Streamlit can't switch the active tab
    # programmatically, so this is the nudge to click it manually.
    st.toast("Game Center is ready on that matchup — click the Game Center tab above.", icon="🏟️")


def jump_to_game_center(week, game_label):
    """Button callback for the Matchups tab's jump control. Callbacks run
    BEFORE the script's next rerun, so setting st.session_state here is
    safe - doing this same assignment inline in the main script body
    would throw StreamlitWidgetAlreadyInstantiatedError, since the Game
    Center tab's selectboxes (which share these keys) are instantiated
    earlier in the same top-to-bottom script pass, before the Matchups
    tab's button code ever runs. Also flips to the Props section, since
    Game Center now lives there - without this, the jump would land on a
    tab that isn't visible until the user switches sides themselves."""
    st.session_state["game_week"] = week
    st.session_state["game_pick"] = game_label
    st.session_state["show_jump_toast"] = True
    st.session_state["site_side"] = "🎯 Props"

def _home_jump(label: str) -> None:
    """Button callback for the Home page's quick-nav row. Same reasoning
    as jump_to_game_center above: this has to run as an on_click callback
    (which Streamlit runs BEFORE the next rerun), not as a plain
    if-button-clicked assignment in the main script body - the "site
    section" radio (key="site_side") has already been instantiated by the
    time this code would otherwise run, and reassigning an instantiated
    widget's session_state key mid-script raises
    StreamlitWidgetAlreadyInstantiatedError."""
    st.session_state["site_side"] = label


if tab_side == "🏠 Dashboard":
    # ---------------- Home (alerts + at-a-glance briefing) ----------------
    st.subheader("🏠 Dashboard")
    st.caption("What needs your attention right now, plus quick links to everything else.")

    home_schedule = get_schedule()
    home_rosters = roster_store.load_rosters(st.secrets)
    rostered_players = {p for r in home_rosters for p in r["players"]}
    home_injuries = get_injuries()
    home_picks = pick_tracker_store.load_picks(st.secrets)

    # severity: "bad" (red) > "warn" (amber) > "good" (green) - sorted so
    # the most urgent thing on the page is always the first thing seen.
    alerts = []

    if rostered_players and not home_injuries.empty:
        rostered_inj = home_injuries[
            home_injuries["player"].isin(rostered_players)
            & home_injuries["report_status"].isin(["Out", "Doubtful", "Questionable", "Injured Reserve", "IR"])
        ]
        for _, inj_row in rostered_inj.iterrows():
            sev = "bad" if inj_row["report_status"] in ("Out", "Injured Reserve", "IR") else "warn"
            detail = f" — {inj_row['report_primary_injury']}" if pd.notna(inj_row.get("report_primary_injury")) else ""
            alerts.append((
                sev, "🩹",
                f"<b>{inj_row['player']}</b> ({inj_row['team']}) is <b>{inj_row['report_status']}</b>{detail} "
                f"— on one of your rosters.",
            ))

    if rostered_players:
        teams_this_week = teams_playing_this_week(home_schedule)
        bye_rows = current_season_df[
            current_season_df["player"].isin(rostered_players) & ~current_season_df["team"].isin(teams_this_week)
        ][["player", "team"]].drop_duplicates()
        for _, bye_row in bye_rows.iterrows():
            alerts.append((
                "warn", "🛌",
                f"<b>{bye_row['player']}</b> ({bye_row['team']}) is on a <b>bye</b> this week — not eligible to start.",
            ))

    if ODDS_API_KEY and get_prop_lines_are_stale():
        alerts.append((
            "warn", "🔑",
            f"Prop odds couldn't refresh this cycle — showing lines from "
            f"{get_prop_lines_updated_at().strftime('%a %-I:%M %p')} instead of a live pull.",
        ))

    resolvable_n = count_resolvable_picks(home_picks, home_schedule)
    if resolvable_n:
        alerts.append((
            "good", "📊",
            f"<b>{resolvable_n} tracked pick(s)</b> have final scores waiting — hit "
            f"<b>Check results now</b> on Track Record to grade them.",
        ))

    sev_style = {"bad": theme.BAD, "warn": theme.WARN, "good": theme.GOOD}
    sev_soft = {"bad": theme.BAD_SOFT, "warn": theme.WARN_SOFT, "good": theme.GOOD_SOFT}
    sev_order = {"bad": 0, "warn": 1, "good": 2}

    if not alerts:
        st.success("✅ All clear — no rostered-player injuries, byes, or stale data to flag right now.")
    else:
        for sev, icon, text in sorted(alerts, key=lambda a: sev_order[a[0]]):
            alert_html = (
                f'<div class="player-card" style="border-left: 4px solid {sev_style[sev]}; '
                f'background: {sev_soft[sev]}; display:flex; align-items:center; gap:14px; '
                f'padding:14px 20px; margin-bottom:10px;">'
                f'<div style="font-size:24px; flex-shrink:0;">{icon}</div>'
                f'<div style="font-size:15px;">{text}</div>'
                f"</div>"
            )
            st.markdown(alert_html, unsafe_allow_html=True)

    st.divider()

    kc1, kc2, kc3, kc4 = st.columns(4)
    kc1.metric("Tracked starters", int(current_season_df["player"].nunique()))

    upcoming_games = home_schedule[home_schedule["home_score"].isna()]
    next_week = int(upcoming_games["week"].min()) if not upcoming_games.empty else None
    games_n = int((upcoming_games["week"] == next_week).sum()) if next_week is not None else 0
    kc2.metric(f"Week {next_week} games" if next_week is not None else "Games this week", games_n if next_week is not None else "—")

    resolved_all = [p for p in home_picks if p["status"] != "Pending"]
    overall_pct, overall_hits, overall_n = compute_hit_rate(resolved_all)
    kc3.metric(
        "Overall hit rate", f"{overall_pct:.0f}%" if overall_pct is not None else "—",
        f"{overall_hits}/{overall_n} resolved" if overall_n else "no picks yet", delta_color="off",
    )

    if not ODDS_API_KEY:
        freshness = "No API key"
    else:
        freshness = "Stale" if get_prop_lines_are_stale() else "Fresh"
    kc4.metric("Prop odds", freshness)

    st.divider()

    anytime_td_home = get_anytime_td_odds()
    if not anytime_td_home.empty:
        top_td = anytime_td_home.sort_values("implied_prob", ascending=False).iloc[0]
        top_match = current_season_df[current_season_df["player"] == top_td["player"]]
        if not top_match.empty:
            info_row = top_match.iloc[0]
            st.markdown("##### 🔥 Today's top mover")
            spotlight_row = {
                "player": top_td["player"], "team": info_row["team"], "position": info_row["position"],
                "headshot_url": info_row.get("headshot_url"), "team_color": info_row.get("team_color") or "#444444",
                "implied_prob": float(top_td["implied_prob"]),
            }
            render_hotpick_cards(
                [spotlight_row],
                body_fn=lambda row: (
                    f'<div class="stat-big">{row["implied_prob"]:.0f}%</div>'
                    f'<div class="stat-label">Anytime TD chance — highest on the board right now</div>'
                ),
                cols_per_row=1, headshot_px=128,
            )
            st.divider()

    st.markdown("##### Jump to")
    nav_targets = [
        ("🔍 Research", "Player stats, deep dives, injuries & matchups"),
        ("🏈 Lineups", "Your rosters & the lineup optimizer"),
        ("🎯 Props", "Prop comparator, First TD, Game Center, bet slips"),
        ("🔥 Hot Picks", "This week's best edges & TD chances"),
        ("📊 Track Record", "Hit rates on everything tracked"),
    ]
    nav_cols = st.columns(5)
    for nav_col, (nav_label, nav_desc) in zip(nav_cols, nav_targets):
        with nav_col:
            st.button(nav_label, use_container_width=True, key=f"home_nav_{nav_label}", on_click=_home_jump, args=(nav_label,))
            st.caption(nav_desc)

elif tab_side == "🔍 Research":
    # ---------------- Overview (current season only) ----------------
    with tab_overview:
        st.sidebar.header("Filters")
        positions = st.sidebar.multiselect(
            "Position", ["QB", "RB", "WR", "TE"], default=["QB", "RB", "WR", "TE"]
        )
        all_teams = sorted(current_season_df["team"].dropna().unique())
        team_filter = st.sidebar.multiselect("Team", all_teams, default=[])
        # Pull every numeric stat column automatically, rather than a fixed
        # short list, so a new stat added upstream shows up here for free.
        # fantasy_points_ppr is pinned first since it's the most common view.
        sort_stat_options = [
            c for c in current_season_df.columns
            if pd.api.types.is_numeric_dtype(current_season_df[c]) and c not in ("season", "week")
        ]
        if "fantasy_points_ppr" in sort_stat_options:
            sort_stat_options.remove("fantasy_points_ppr")
            sort_stat_options.insert(0, "fantasy_points_ppr")
        sort_stat = st.sidebar.selectbox("Sort / rank by", sort_stat_options, index=0)

        view = current_season_df[current_season_df["position"].isin(positions)]
        if team_filter:
            view = view[view["team"].isin(team_filter)]

        summary_df = build_player_summary(view, sort_stat)

        if summary_df.empty:
            st.info("No players match the current filters, or the season hasn't started yet.")
        else:
            cnt_col, badge_info_col = st.columns([5, 1])
            with cnt_col:
                st.caption(f"{len(summary_df)} players — {CURRENT_SEASON} season, ranked by {sort_stat.replace('_', ' ')}")
            with badge_info_col:
                theme.info_popover(
                    "**Badge key:** matchup badges show the upcoming opponent's defensive rank (color: red = "
                    "toughest, green = easiest). 🎯 Anytime TD is the betting market's implied chance this player "
                    "scores any touchdown this week; 🥇 First TD is the narrower chance they score the game's "
                    "FIRST touchdown (see the First TD tab in Props for the full breakdown) — both "
                    "are market probabilities, not Prop Shop projections. Hover a badge for details.",
                    label="ℹ️ Badge key",
                )
            render_player_cards(summary_df, sort_stat, cols_per_row=4)

    # ---------------- Player Deep Dive (full history) ----------------
    with tab_deep_dive:
        player_list = sorted(merged["player"].unique())
        compare_mode = st.checkbox("Compare with another player")
        home_away_lookup = build_home_away_lookup(get_all_seasons_schedule())

        def render_header_and_metrics(pdf_current: pd.DataFrame, pdf_full: pd.DataFrame):
            info = pdf_full.iloc[0]
            c1, c2 = st.columns([1, 4])
            with c1:
                if pd.notna(info.get("headshot_url")):
                    st.image(sized_headshot(info["headshot_url"], 220), width=220)
            with c2:
                st.markdown(f"### {info['player']}")
                st.markdown(f"##### {team_logo_html(info['team'], px=36)}{info['position']} · {info['team']}", unsafe_allow_html=True)
                matchup_result = get_matchup_label(info["team"], info["position"], "fantasy_points_ppr")
                if matchup_result:
                    matchup_text, matchup_rank = matchup_result
                    st.markdown(matchup_badge_html(matchup_text, matchup_rank, tag="span"), unsafe_allow_html=True)
                td_odds = get_anytime_td_odds()
                td_match = td_odds[td_odds["player"] == info["player"]] if not td_odds.empty else td_odds
                if not td_match.empty:
                    st.markdown(anytime_td_badge_html(float(td_match["implied_prob"].iloc[0]), tag="span"), unsafe_allow_html=True)
                first_td_odds = get_first_td_odds()
                first_td_match = first_td_odds[first_td_odds["player"] == info["player"]] if not first_td_odds.empty else first_td_odds
                if not first_td_match.empty:
                    st.markdown(first_td_badge_html(float(first_td_match["implied_prob"].iloc[0]), tag="span"), unsafe_allow_html=True)

            metrics_source = pdf_current if not pdf_current.empty else pdf_full
            avg, last, trend, consistency, _cv = compute_summary(metrics_source, "fantasy_points_ppr")
            label_suffix = f"({CURRENT_SEASON})" if not pdf_current.empty else "(no current-season games yet)"
            m1, m2 = st.columns(2)
            m1.metric(f"Avg PPR {label_suffix}", f"{avg:.1f}")
            m1.metric("Games Played", len(metrics_source))
            m2.metric("Last Game (PPR)", f"{last:.1f}", delta=f"{trend:+.1f}")
            m2.metric("Consistency", consistency)

        if not compare_mode:
            selected_player = st.selectbox("Choose a player", player_list)
            pdf_full = with_period_label(merged[merged["player"] == selected_player])
            pdf_full = add_matchup_display(pdf_full, home_away_lookup)
            pdf_current = pdf_full[pdf_full["season"] == CURRENT_SEASON]

            if pdf_full.empty:
                st.info("No data for this player yet.")
            else:
                render_header_and_metrics(pdf_current, pdf_full)

                numeric_cols = [
                    c for c in pdf_full.columns
                    if pd.api.types.is_numeric_dtype(pdf_full[c]) and c not in ("season", "week")
                ]
                default_idx = numeric_cols.index("fantasy_points_ppr") if "fantasy_points_ppr" in numeric_cols else 0
                stat = st.selectbox("Stat to chart", numeric_cols, index=default_idx)

                period_order = chronological_order(pdf_full)
                stat_title = stat.replace("_", " ").title()
                line = alt.Chart(pdf_full).mark_line(point=True, color=theme.ACCENT).encode(
                    x=alt.X("period:N", sort=period_order, title=None),
                    y=alt.Y(f"{stat}:Q", title=stat_title),
                    tooltip=[
                        alt.Tooltip("period:N", title="Week"),
                        alt.Tooltip("matchup_display:N", title="Matchup"),
                        alt.Tooltip(f"{stat}:Q", title=stat_title, format=".1f"),
                    ],
                )

                layers = [line]
                legend_bits = []

                # Season-average reference line
                avg_source = pdf_current if not pdf_current.empty else pdf_full
                if stat in avg_source.columns and not avg_source[stat].dropna().empty:
                    stat_avg = float(avg_source[stat].mean())
                    avg_rule = alt.Chart(pd.DataFrame({"y": [stat_avg]})).mark_rule(
                        color=theme.SUB, strokeDash=[5, 4], size=2
                    ).encode(y="y:Q", tooltip=alt.value(f"Season avg: {stat_avg:.1f}"))
                    layers.append(avg_rule)
                    legend_bits.append(f"⬤ ---- Season avg ({stat_avg:.1f})")

                # Live prop-line reference, when this stat has a mapped market
                prop_market = PROP_MARKET_MAP.get(stat)
                if prop_market:
                    live_lines = get_prop_lines()
                    match = live_lines[(live_lines["player"] == selected_player) & (live_lines["market"] == prop_market)] if not live_lines.empty else pd.DataFrame()
                    if not match.empty:
                        prop_val = float(match["point"].iloc[0])
                        prop_rule = alt.Chart(pd.DataFrame({"y": [prop_val]})).mark_rule(
                            color=theme.INK, strokeDash=[2, 2], size=2
                        ).encode(y="y:Q", tooltip=alt.value(f"Prop line: {prop_val:.1f}"))
                        layers.append(prop_rule)
                        legend_bits.append(f"⬤ ···· Prop line ({prop_val:.1f})")

                st.altair_chart(alt.layer(*layers).properties(height=300), use_container_width=True)
                if legend_bits:
                    st.caption("  ".join(legend_bits))

                if not pdf_current.empty:
                    st.subheader(f"3-Week Rolling Average ({CURRENT_SEASON})")
                    rolling = pdf_current[numeric_cols].rolling(3, min_periods=1).mean()
                    rolling.insert(0, "week", pdf_current["week"].values)
                    st.dataframe(rolling.set_index("week"), width="content", row_height=38)

                with st.expander("Full weekly stats (all seasons)"):
                    st.dataframe(pdf_full.drop(columns=["period"]), width="content", row_height=38)

        else:
            col_a, col_b = st.columns(2)
            with col_a:
                player_a = st.selectbox("Player A", player_list, index=0, key="cmp_a")
            with col_b:
                default_b_index = 1 if len(player_list) > 1 else 0
                player_b = st.selectbox("Player B", player_list, index=default_b_index, key="cmp_b")

            pdf_a_full = with_period_label(merged[merged["player"] == player_a])
            pdf_b_full = with_period_label(merged[merged["player"] == player_b])
            pdf_a_full = add_matchup_display(pdf_a_full, home_away_lookup)
            pdf_b_full = add_matchup_display(pdf_b_full, home_away_lookup)
            pdf_a_current = pdf_a_full[pdf_a_full["season"] == CURRENT_SEASON]
            pdf_b_current = pdf_b_full[pdf_b_full["season"] == CURRENT_SEASON]

            if pdf_a_full.empty or pdf_b_full.empty:
                st.info("No data available for one or both players yet.")
            else:
                col_a, col_b = st.columns(2)
                with col_a:
                    render_header_and_metrics(pdf_a_current, pdf_a_full)
                with col_b:
                    render_header_and_metrics(pdf_b_current, pdf_b_full)

                numeric_cols_a = [
                    c for c in pdf_a_full.columns
                    if pd.api.types.is_numeric_dtype(pdf_a_full[c]) and c not in ("season", "week")
                ]
                numeric_cols_b = [
                    c for c in pdf_b_full.columns
                    if pd.api.types.is_numeric_dtype(pdf_b_full[c]) and c not in ("season", "week")
                ]
                shared_cols = [c for c in numeric_cols_a if c in numeric_cols_b]

                if not shared_cols:
                    st.info("These two players don't share any comparable stat columns.")
                else:
                    default_idx = shared_cols.index("fantasy_points_ppr") if "fantasy_points_ppr" in shared_cols else 0
                    stat = st.selectbox("Stat to compare", shared_cols, index=default_idx, key="cmp_stat")

                    long_df = pd.concat([
                        pdf_a_full[["period", "season", "week", "matchup_display", stat]].assign(player=player_a),
                        pdf_b_full[["period", "season", "week", "matchup_display", stat]].assign(player=player_b),
                    ])
                    player_colors = [theme.ACCENT, theme.INK]
                    color_scale = alt.Scale(domain=[player_a, player_b], range=player_colors)
                    period_order = chronological_order(pdf_a_full, pdf_b_full)
                    cmp_stat_title = stat.replace("_", " ").title()
                    combo_chart = alt.Chart(long_df).mark_line(point=True).encode(
                        x=alt.X("period:N", sort=period_order, title=None),
                        y=alt.Y(f"{stat}:Q", title=cmp_stat_title),
                        color=alt.Color("player:N", scale=color_scale, legend=alt.Legend(title=None)),
                        tooltip=[
                            alt.Tooltip("player:N", title="Player"),
                            alt.Tooltip("period:N", title="Week"),
                            alt.Tooltip("matchup_display:N", title="Matchup"),
                            alt.Tooltip(f"{stat}:Q", title=cmp_stat_title, format=".1f"),
                        ],
                    )

                    # Edge is based on current-season averages when available, else full history
                    a_src = pdf_a_current if not pdf_a_current.empty else pdf_a_full
                    b_src = pdf_b_current if not pdf_b_current.empty else pdf_b_full
                    avg_a = a_src[stat].mean()
                    avg_b = b_src[stat].mean()

                    cmp_layers = [combo_chart]
                    cmp_legend_bits = []
                    prop_market = PROP_MARKET_MAP.get(stat)
                    live_lines = get_prop_lines() if prop_market else pd.DataFrame()

                    for pname, pavg, pcolor in [(player_a, avg_a, player_colors[0]), (player_b, avg_b, player_colors[1])]:
                        if pd.notna(pavg):
                            avg_rule = alt.Chart(pd.DataFrame({"y": [pavg]})).mark_rule(
                                color=pcolor, strokeDash=[5, 4], size=2, opacity=0.6
                            ).encode(y="y:Q", tooltip=alt.value(f"{pname} avg: {pavg:.1f}"))
                            cmp_layers.append(avg_rule)
                            cmp_legend_bits.append(f"⬤ ---- {pname} avg ({pavg:.1f})")

                        if prop_market and not live_lines.empty:
                            match = live_lines[(live_lines["player"] == pname) & (live_lines["market"] == prop_market)]
                            if not match.empty:
                                prop_val = float(match["point"].iloc[0])
                                prop_rule = alt.Chart(pd.DataFrame({"y": [prop_val]})).mark_rule(
                                    color=pcolor, strokeDash=[2, 2], size=2
                                ).encode(y="y:Q", tooltip=alt.value(f"{pname} prop line: {prop_val:.1f}"))
                                cmp_layers.append(prop_rule)
                                cmp_legend_bits.append(f"⬤ ···· {pname} prop line ({prop_val:.1f})")

                    st.altair_chart(alt.layer(*cmp_layers).properties(height=300), use_container_width=True)
                    if cmp_legend_bits:
                        st.caption("  ".join(cmp_legend_bits))
                    if avg_a > avg_b:
                        edge = player_a
                    elif avg_b > avg_a:
                        edge = player_b
                    else:
                        edge = "Even"
                    st.caption(
                        f"Average edge on {stat.replace('_', ' ')}: "
                        f"**{edge}** ({avg_a:.1f} vs {avg_b:.1f})"
                    )

                with st.expander(f"Full weekly stats — {player_a}"):
                    st.dataframe(pdf_a_full.drop(columns=["period"]), width="content", row_height=38)
                with st.expander(f"Full weekly stats — {player_b}"):
                    st.dataframe(pdf_b_full.drop(columns=["period"]), width="content", row_height=38)

    # ---------------- Injuries (full league injury report) ----------------
    with tab_injuries:
        st.subheader("Injury Report")

        all_injuries = get_injuries()
        if all_injuries.empty:
            st.info("No injury report available yet this week.")
        else:
            inj_cap_col, inj_info_col = st.columns([5, 1])
            with inj_cap_col:
                st.caption(f"Week {int(all_injuries['week'].iloc[0])} ({CURRENT_SEASON} season) — full league injury report.")
            with inj_info_col:
                theme.info_popover(
                    "Every player on the official NFL injury report - not just tracked starters, so you can "
                    "catch handcuffs and breakout candidates too. Sourced from nflverse's copy of the official "
                    "team-submitted reports.",
                    label="ℹ️ About this report",
                )

            STATUS_EMOJI = {"Out": "🔴", "Doubtful": "🟠", "Questionable": "🟡", "Injured Reserve": "🔴", "IR": "🔴"}
            STATUS_ORDER = {"Out": 0, "Doubtful": 1, "Questionable": 2}

            inj_df = all_injuries.copy()
            inj_df["status_rank"] = inj_df["report_status"].map(STATUS_ORDER).fillna(3)
            tracked_players = set(current_season_df["player"].unique())
            inj_df["Tracked"] = inj_df["player"].isin(tracked_players).map({True: "✅", False: ""})
            inj_df["Team Logo"] = inj_df["team"].map(team_logos)
            inj_df["Status"] = inj_df["report_status"].apply(
                lambda s: f"{STATUS_EMOJI.get(s, '⚪')} {s}" if pd.notna(s) else "—"
            )

            ic1, ic2, ic3 = st.columns(3)
            with ic1:
                team_opts = sorted(inj_df["team"].dropna().unique())
                team_pick = st.multiselect("Team", team_opts, key="injury_team_filter")
            with ic2:
                status_opts = sorted(inj_df["report_status"].dropna().unique())
                status_pick = st.multiselect("Status", status_opts, default=status_opts, key="injury_status_filter")
            with ic3:
                if "position" in inj_df.columns:
                    pos_opts = sorted(inj_df["position"].dropna().unique())
                    pos_pick = st.multiselect("Position", pos_opts, key="injury_position_filter")
                else:
                    pos_pick = []

            filtered_inj = inj_df[inj_df["report_status"].isin(status_pick)] if status_pick else inj_df
            if team_pick:
                filtered_inj = filtered_inj[filtered_inj["team"].isin(team_pick)]
            if pos_pick:
                filtered_inj = filtered_inj[filtered_inj["position"].isin(pos_pick)]
            filtered_inj = filtered_inj.sort_values(["status_rank", "team", "player"])

            if filtered_inj.empty:
                st.info("No players match the current filters.")
            else:
                st.caption(f"{len(filtered_inj)} players")
                display_cols = ["Tracked", "player", "Team Logo", "team"]
                if "position" in filtered_inj.columns:
                    display_cols.append("position")
                display_cols += ["Status", "report_primary_injury", "practice_status"]
                rename_map = {
                    "player": "Player", "team": "Team", "position": "Pos",
                    "report_primary_injury": "Injury", "practice_status": "Practice",
                }
                shown = filtered_inj[display_cols].rename(columns=rename_map)
                st.dataframe(
                    shown, width="content", hide_index=True, row_height=44,
                    column_config={
                        "Tracked": st.column_config.TextColumn(width="small"),
                        "Player": st.column_config.TextColumn(width=200),
                        "Team Logo": st.column_config.ImageColumn(" ", width=70),
                        "Team": st.column_config.TextColumn(width="small"),
                        "Pos": st.column_config.TextColumn(width="small"),
                        "Status": st.column_config.TextColumn(width=140),
                        "Injury": st.column_config.TextColumn(width=180),
                        "Practice": st.column_config.TextColumn(width=240),
                    },
                )
                st.caption(
                    "🔴 Out  🟠 Doubtful  🟡 Questionable  ⚪ other designation (e.g. Probable). "
                    "✅ Tracked = one of this app's auto-tracked starters."
                )

    # ---------------- Matchups (game lines, spreads, totals, weather) ----------------
    with tab_matchups:
        st.subheader("Team Matchups")
        st.caption("Game lines, spreads, totals and weather for every game in a week - built for scanning the whole slate at once, not just one game.")

        schedule_all = get_schedule().copy()
        schedule_all["gameday_fmt"] = pd.to_datetime(schedule_all["gameday"]).dt.strftime("%a %-m/%-d")
        schedule_all["gametime_fmt"] = schedule_all["gametime"].apply(format_gametime) if "gametime" in schedule_all.columns else ""
        schedule_all["game_label"] = (
            schedule_all["gameday_fmt"] + (" " + schedule_all["gametime_fmt"]).where(schedule_all["gametime_fmt"] != "", "")
            + " — " + schedule_all["away_team"] + " @ " + schedule_all["home_team"]
        )
        # team_logos/team_logo_html are defined once near the top of the file
        # (right after current_season_df) and reused everywhere a team shows up.

        weeks_available_m = sorted(schedule_all["week"].unique())
        upcoming_m = schedule_all[schedule_all["home_score"].isna()]
        default_week_m = int(upcoming_m["week"].min()) if not upcoming_m.empty else int(schedule_all["week"].max())
        if "matchup_week" not in st.session_state:
            st.session_state["matchup_week"] = default_week_m if default_week_m in weeks_available_m else weeks_available_m[0]
        matchup_week = st.selectbox("Week", weeks_available_m, key="matchup_week")

        week_slate = schedule_all[schedule_all["week"] == matchup_week].sort_values("gameday")

        if week_slate.empty:
            st.info("No games scheduled for this week.")
        else:
            sort_col1, sort_col2 = st.columns([2, 1])
            with sort_col1:
                sort_choice = st.selectbox(
                    "Sort by", ["Kickoff (chronological)", "Highest total", "Biggest spread", "Worst weather"],
                    key="matchup_sort",
                )
            with sort_col2:
                group_by_day = st.checkbox(
                    "Group by day", value=True, key="matchup_group_by_day",
                    help="Only applies when sorting by kickoff.",
                )
            group_by_day = group_by_day and sort_choice == "Kickoff (chronological)"

            rows = []
            for _, g in week_slate.iterrows():
                is_played = pd.notna(g.get("home_score"))
                home, away = g["home_team"], g["away_team"]

                spread_val = g.get("spread_line")
                if pd.notna(spread_val):
                    fav = home if spread_val < 0 else away
                    spread_text = f"{fav} {-abs(spread_val):.1f}"
                else:
                    spread_text = "—"
                total_val = g.get("total_line")
                total_text = f"{total_val:.1f}" if pd.notna(total_val) else "—"
                home_imp, away_imp = implied_totals(spread_val, total_val, home, away)

                weather_risk = None
                if is_played and pd.notna(g.get("temp")):
                    weather_text = f"{int(g['temp'])}°F / {int(g.get('wind', 0) or 0)} mph wind (actual)"
                else:
                    w = weather_badge(home, g.get("gameday"), g.get("roof"), g.get("location"))
                    if w:
                        weather_text, weather_risk = w
                        weather_text = f"{weather_text} (forecast)"
                    elif pd.notna(g.get("roof")) and g["roof"] in INDOOR_ROOF_STATES:
                        weather_text = "Indoors"
                    else:
                        weather_text = "—"

                rows.append({
                    "day_name": pd.Timestamp(g["gameday"]).day_name() if pd.notna(g.get("gameday")) else "TBD",
                    "gameday": g.get("gameday"),
                    "Kickoff": (g.get("gameday_fmt", "") + (" " + g["gametime_fmt"] if g.get("gametime_fmt") else "")).strip(),
                    "Away Logo": team_logos.get(away),
                    "Away": away,
                    "Home Logo": team_logos.get(home),
                    "Home": home,
                    "Spread": spread_text,
                    "Total": total_text,
                    "total_sort": total_val if pd.notna(total_val) else -1,
                    "spread_sort": abs(spread_val) if pd.notna(spread_val) else -1,
                    "Implied Away": away_imp if away_imp is not None else "—",
                    "Implied Home": home_imp if home_imp is not None else "—",
                    "Roof": g["roof"].title() if pd.notna(g.get("roof")) else "—",
                    "Weather": weather_text,
                    "Weather Risk": weather_risk,
                    "Result": f"{away} {int(g['away_score'])} - {int(g['home_score'])} {home}" if is_played else "—",
                })

            matchups_df = pd.DataFrame(rows)

            # ---- Total-points bar chart: which games project as the highest/lowest scoring ----
            chart_df = matchups_df[matchups_df["total_sort"] > 0].copy()
            if not chart_df.empty:
                chart_df["matchup_label"] = chart_df["Away"] + " @ " + chart_df["Home"] + " (" + chart_df["Spread"] + ")"
                totals_chart = (
                    alt.Chart(chart_df)
                    .mark_bar()
                    .encode(
                        x=alt.X("total_sort:Q", title="Game total"),
                        y=alt.Y("matchup_label:N", sort="-x", title=None),
                        color=alt.Color(
                            "total_sort:Q", title="Total",
                            scale=alt.Scale(range=["#5A4A22", theme.ACCENT]),
                            legend=None,
                        ),
                        tooltip=[
                            alt.Tooltip("matchup_label:N", title="Game"),
                            alt.Tooltip("total_sort:Q", title="Total", format=".1f"),
                            alt.Tooltip("Implied Away:N", title="Implied away"),
                            alt.Tooltip("Implied Home:N", title="Implied home"),
                            alt.Tooltip("Weather:N", title="Weather"),
                        ],
                    )
                    .properties(height=max(28 * len(chart_df), 120))
                )
                st.altair_chart(totals_chart, use_container_width=True)
            else:
                st.info("No totals posted yet for this week.")

            # ---- Sort/group the table itself ----
            if sort_choice == "Highest total":
                matchups_df = matchups_df.sort_values("total_sort", ascending=False)
            elif sort_choice == "Biggest spread":
                matchups_df = matchups_df.sort_values("spread_sort", ascending=False)
            elif sort_choice == "Worst weather":
                matchups_df = matchups_df.sort_values("Weather Risk", ascending=False, na_position="last")
            else:
                matchups_df = matchups_df.sort_values("gameday")

            display_cols = [
                "Kickoff", "Away Logo", "Away", "Home Logo", "Home", "Spread", "Total",
                "Implied Away", "Implied Home", "Roof", "Weather", "Weather Risk", "Result",
            ]
            column_config = {
                "Kickoff": st.column_config.TextColumn(width=175),
                "Away Logo": st.column_config.ImageColumn(" ", width=80),
                "Away": st.column_config.TextColumn(width="small"),
                "Home Logo": st.column_config.ImageColumn(" ", width=80),
                "Home": st.column_config.TextColumn(width="small"),
                "Spread": st.column_config.TextColumn(width="small"),
                "Total": st.column_config.TextColumn(width="small"),
                "Implied Away": st.column_config.TextColumn(width="small"),
                "Implied Home": st.column_config.TextColumn(width="small"),
                "Roof": st.column_config.TextColumn(width=100),
                "Weather": st.column_config.TextColumn(width=160),
                "Weather Risk": st.column_config.ProgressColumn(
                    "Weather Risk", min_value=0, max_value=100, format="%.0f%%", width=130,
                    help="Rough wind/rain severity score - higher means more likely to affect passing and kicking.",
                ),
                "Result": st.column_config.TextColumn(width=140),
            }

            if group_by_day:
                for day in matchups_df.sort_values("gameday")["day_name"].unique():
                    day_df = matchups_df[matchups_df["day_name"] == day]
                    st.markdown(f"**{day}**")
                    st.dataframe(day_df[display_cols], width="content", hide_index=True, column_config=column_config, row_height=50)
            else:
                st.dataframe(matchups_df[display_cols], width="content", hide_index=True, column_config=column_config, row_height=50)

            _, mm_info_col = st.columns([5, 1])
            with mm_info_col:
                theme.info_popover(
                    "**\"Away @ Home\" convention throughout** - the Home column is the team hosting. Implied "
                    "totals split the game total by the spread. Weather is a live forecast (Open-Meteo, refreshes "
                    "every few hours) for games within about 16 days, or the actual recorded conditions for games "
                    "already played. Domed/closed-roof and neutral-site games show no weather since it doesn't "
                    "apply or the venue differs from the home team's usual city.",
                    label="ℹ️ How to read this table",
                )

            st.divider()
            jump_col1, jump_col2 = st.columns([3, 1])
            with jump_col1:
                jump_pick = st.selectbox("Open a game in Game Center", week_slate["game_label"].tolist(), key="matchup_jump_pick")
            with jump_col2:
                st.write("")
                st.button(
                    "Open →", key="matchup_jump_button",
                    on_click=jump_to_game_center, args=(matchup_week, jump_pick),
                )
elif tab_side == "🏈 Lineups":
    # ---------------- My Rosters ----------------
    with tab_rosters:
        render_rosters_tab()
    # ---------------- Lineup Optimizer ----------------
    with tab_lineup:
        render_lineup_tab()
elif tab_side == "🎯 Props":
    # ---------------- Prop Comparator (current season only) ----------------
    with tab_props:
        st.subheader("Player Prop Line Comparator")
        st.caption(f"Pick a player and stat, enter a prop line, and see how they've done this {CURRENT_SEASON} season.")

        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            prop_player = st.selectbox("Player", sorted(current_season_df["player"].unique()), key="prop_player")

        prop_pdf = current_season_df[current_season_df["player"] == prop_player].sort_values("week")
        position = prop_pdf["position"].iloc[0] if not prop_pdf.empty else "QB"
        available_stats = [s for s in PROP_STATS_BY_POSITION.get(position, []) if s in prop_pdf.columns]

        if not prop_pdf.empty:
            prop_team = prop_pdf["team"].iloc[0]
            st.markdown(f"##### {team_logo_html(prop_team, px=32)}{position} · {prop_team}", unsafe_allow_html=True)
            prop_td_odds = get_anytime_td_odds()
            prop_td_match = prop_td_odds[prop_td_odds["player"] == prop_player] if not prop_td_odds.empty else prop_td_odds
            if not prop_td_match.empty:
                st.markdown(anytime_td_badge_html(float(prop_td_match["implied_prob"].iloc[0]), tag="span"), unsafe_allow_html=True)
            prop_first_td_odds = get_first_td_odds()
            prop_first_td_match = prop_first_td_odds[prop_first_td_odds["player"] == prop_player] if not prop_first_td_odds.empty else prop_first_td_odds
            if not prop_first_td_match.empty:
                st.markdown(first_td_badge_html(float(prop_first_td_match["implied_prob"].iloc[0]), tag="span"), unsafe_allow_html=True)

        with c2:
            prop_stat = st.selectbox("Stat", available_stats, key="prop_stat") if available_stats else None
        with c3:
            # Pre-fill with the live market line when we have one, else season average
            live_lines = get_prop_lines()
            market = PROP_MARKET_MAP.get(prop_stat) if prop_stat else None
            live_match = live_lines[(live_lines["player"] == prop_player) & (live_lines["market"] == market)] if market is not None and not live_lines.empty else pd.DataFrame()
            # Captured here (rather than re-looked-up below) because it's
            # only meaningful for the actual live market line - if the
            # person then edits the Prop line box to a different number,
            # this fair probability no longer describes that number, so
            # it's shown tagged to the live line it came from, not
            # silently re-attached to whatever they typed.
            live_line_val = None
            live_fair_prob_over = None
            if not live_match.empty:
                default_line = round(float(live_match["point"].iloc[0]), 1)
                live_line_val = default_line
                if "fair_prob_over" in live_match.columns:
                    raw_fpo = live_match["fair_prob_over"].iloc[0]
                    if pd.notna(raw_fpo):
                        live_fair_prob_over = float(raw_fpo)
            elif prop_stat and not prop_pdf.empty:
                default_line = round(float(prop_pdf[prop_stat].mean()), 1)
            else:
                default_line = 0.0
            prop_line = st.number_input("Prop line", value=default_line, step=0.5)

        if prop_stat and not prop_pdf.empty:
            team = prop_pdf["team"].iloc[0]
            next_matchup = get_matchup_label(team, position, prop_stat)
            if next_matchup:
                next_text, next_rank = next_matchup
                st.markdown(matchup_badge_html(f"Next: {next_text}", next_rank, tag="span"), unsafe_allow_html=True)
            if live_fair_prob_over is not None:
                st.markdown(
                    fair_prob_badge_html(live_fair_prob_over, "▲ Over", tag="span")
                    + f' <span class="stat-label">market fair prob at the live line ({live_line_val:.1f})</span>',
                    unsafe_allow_html=True,
                )
            prop_pdf = prop_pdf.copy()
            prop_pdf["result"] = prop_pdf[prop_stat].apply(
                lambda x: "✅ Over" if x > prop_line else ("❌ Under" if x < prop_line else "➖ Push")
            )
            prop_pdf["result_plain"] = prop_pdf[prop_stat].apply(
                lambda x: "Over" if x > prop_line else ("Under" if x < prop_line else "Push")
            )
            prop_pdf["week_label"] = "Week " + prop_pdf["week"].astype(str)
            hit_rate = (prop_pdf[prop_stat] > prop_line).mean() * 100

            m1, m2, m3 = st.columns(3)
            m1.metric(f"Hit rate (Over {prop_line})", f"{hit_rate:.0f}%")
            m2.metric(f"{CURRENT_SEASON} Average", f"{prop_pdf[prop_stat].mean():.1f}")
            m3.metric("Games Played", len(prop_pdf))

            bars = (
                alt.Chart(prop_pdf)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                .encode(
                    x=alt.X("week_label:N", title=None, sort=None),
                    y=alt.Y(f"{prop_stat}:Q", title=prop_stat.replace("_", " ").title()),
                    color=alt.Color(
                        "result_plain:N",
                        scale=alt.Scale(domain=["Over", "Under", "Push"], range=[theme.GOOD, theme.BAD, theme.SUB]),
                        legend=alt.Legend(title=None),
                    ),
                    tooltip=["week_label", prop_stat, "opponent_team", "result"],
                )
            )
            rule = alt.Chart(pd.DataFrame({"y": [prop_line]})).mark_rule(
                color=theme.INK, strokeDash=[6, 4], size=2
            ).encode(y="y:Q")
            st.altair_chart((bars + rule).properties(height=320), use_container_width=True)

            # Matchup difficulty: how tough was that week's opponent against this stat/position?
            rank_col = f"{prop_stat}_rank"
            defense_ranks = get_defense_ranks()
            pos_ranks = defense_ranks[defense_ranks["position"] == position]

            if rank_col in pos_ranks.columns and "opponent_team" in prop_pdf.columns:
                prop_pdf = prop_pdf.merge(
                    pos_ranks[["team", rank_col]], left_on="opponent_team", right_on="team", how="left"
                )
                prop_pdf["matchup"] = prop_pdf.apply(
                    lambda r: f"{r['opponent_team']} (#{int(r[rank_col])} toughest vs {position})"
                    if pd.notna(r.get(rank_col)) else str(r["opponent_team"]),
                    axis=1,
                )
            else:
                prop_pdf["matchup"] = prop_pdf.get("opponent_team", "")

            display = prop_pdf[["week", "matchup", prop_stat, "result"]].rename(columns={prop_stat: "actual"})
            st.dataframe(display, width="content", hide_index=True, row_height=38)
            st.caption(
                f"Matchup rank is out of 32, based on {CURRENT_SEASON} season totals allowed to that position "
                "(#1 = toughest defense, #32 = easiest)."
            )
        else:
            st.info("No stats available for this player yet this season.")

    # ---------------- First TD (dedicated view/analytics) ----------------
    with tab_firsttd:
        st.subheader("First Touchdown Scorer")
        st.caption(
            "Who's most likely to score the FIRST touchdown of their game this week - a much narrower, "
            "more concentrated bet than \"any\" touchdown. Odds come from The Odds API's player_1st_td market."
        )

        first_td_odds = get_first_td_odds()

        if first_td_odds.empty:
            st.info(
                "No First TD odds available right now - either the odds pull hasn't found this market yet, "
                "the safety buffer paused today's refresh, or it's off-season/bye week for every tracked team. "
                "Try \"Refresh prop lines now\" above.",
            )
        else:
            anytime_td_odds = get_anytime_td_odds()
            schedule_for_ftd = get_schedule()
            next_opp_map_ftd = build_next_opponent_map(schedule_for_ftd)

            # One row per tracked player with First TD odds - pull in season
            # context (TDs, games, rate) and the anytime-TD number for the
            # "share of anytime" comparison below, the same way
            # build_player_summary does for the Overview cards.
            ftd_rows = []
            for player, pdf in current_season_df.groupby("player"):
                match = first_td_odds[first_td_odds["player"] == player]
                if match.empty:
                    continue
                first_pct = float(match["implied_prob"].iloc[0])

                first_row = pdf.iloc[0]
                team = first_row["team"]
                position = first_row["position"]

                anytime_match = anytime_td_odds[anytime_td_odds["player"] == player] if not anytime_td_odds.empty else pd.DataFrame()
                anytime_pct = float(anytime_match["implied_prob"].iloc[0]) if not anytime_match.empty else None
                # How concentrated this player is as their team's early scoring
                # threat: what share of their overall TD chance is specifically
                # for being FIRST. Only meaningful when they actually have an
                # anytime-TD price to divide into - a tiny anytime number with
                # no first-TD context isn't a useful ratio.
                share_of_anytime = round((first_pct / anytime_pct) * 100) if anytime_pct else None

                total_tds = 0
                for col in ("rushing_tds", "receiving_tds"):
                    if col in pdf.columns:
                        total_tds += pdf[col].sum()
                games_played = pdf["week"].nunique()
                tds_per_game = round(total_tds / games_played, 2) if games_played else 0.0

                opp_row = next_opp_map_ftd[next_opp_map_ftd["team"] == team]
                if not opp_row.empty:
                    is_home = bool(opp_row["is_home"].iloc[0])
                    opponent_text = f"{'vs' if is_home else '@'} {opp_row['opponent'].iloc[0]}"
                    kickoff = opp_row["kickoff"].iloc[0] if "kickoff" in opp_row.columns else ""
                else:
                    opponent_text = "Bye / no game"
                    kickoff = ""

                ftd_rows.append({
                    "player": player,
                    "team": team,
                    "position": position,
                    "headshot_url": first_row.get("headshot_url"),
                    "team_color": first_row.get("team_color") or "#444444",
                    "opponent": opponent_text,
                    "kickoff": kickoff,
                    "first_td_pct": first_pct,
                    "anytime_td_pct": anytime_pct,
                    "share_of_anytime": share_of_anytime,
                    "season_tds": int(total_tds),
                    "tds_per_game": tds_per_game,
                })

            ftd_df = pd.DataFrame(ftd_rows)

            if ftd_df.empty:
                st.info("First TD odds came back, but none matched a currently tracked starter.")
            else:
                f1, f2 = st.columns(2)
                with f1:
                    ftd_positions = st.multiselect(
                        "Position", ["QB", "RB", "WR", "TE"], default=["RB", "WR", "TE"], key="ftd_positions",
                    )
                with f2:
                    ftd_teams = st.multiselect("Team", sorted(ftd_df["team"].dropna().unique()), default=[], key="ftd_teams")

                filtered = ftd_df[ftd_df["position"].isin(ftd_positions)] if ftd_positions else ftd_df
                if ftd_teams:
                    filtered = filtered[filtered["team"].isin(ftd_teams)]
                filtered = filtered.sort_values("first_td_pct", ascending=False)

                if filtered.empty:
                    st.info("No players match the current filters.")
                else:
                    st.markdown("##### This week's favorites")
                    top3 = filtered.head(3)

                    def _first_td_podium_body(row: dict) -> str:
                        return (
                            f'<div class="stat-big">{row["first_td_pct"]:.0f}%</div>'
                            f'<div class="stat-label">First TD chance</div>'
                        )

                    # Same large Hot-Picks-style card (with the 🥇🥈🥉
                    # podium treatment) as the Hot Picks page's own top
                    # picks - this IS the pattern Hot Picks was modeled on,
                    # so it now shares the exact same component.
                    render_hotpick_cards(
                        top3.to_dict("records"), body_fn=_first_td_podium_body,
                        cols_per_row=len(top3), headshot_px=128, medals=True,
                    )

                    st.markdown("##### Full board")
                    display_cols = filtered[[
                        "player", "team", "position", "opponent", "kickoff",
                        "first_td_pct", "anytime_td_pct", "share_of_anytime", "season_tds", "tds_per_game",
                    ]].rename(columns={
                        "player": "Player", "team": "Team", "position": "Pos", "opponent": "Opponent",
                        "kickoff": "Kickoff", "first_td_pct": "First TD %", "anytime_td_pct": "Anytime TD %",
                        "share_of_anytime": "Share of Anytime %", "season_tds": f"{CURRENT_SEASON} TDs",
                        "tds_per_game": "TDs/Game",
                    })
                    st.dataframe(
                        display_cols, width="content", hide_index=True, row_height=38,
                        column_config={
                            "First TD %": st.column_config.NumberColumn(format="%.0f%%"),
                            "Anytime TD %": st.column_config.NumberColumn(format="%.0f%%"),
                            "Share of Anytime %": st.column_config.NumberColumn(format="%.0f%%"),
                            "TDs/Game": st.column_config.NumberColumn(format="%.2f"),
                        },
                    )
                    st.caption(
                        "**Share of Anytime %** = First TD chance ÷ Anytime TD chance. A high share means most of "
                        "this player's touchdown equity comes from getting there FIRST (an early-game, "
                        "concentrated role) rather than just scoring at some point. Both percentages are the "
                        "betting market's implied probability, including the sportsbook's margin - not a Prop "
                        "Shop projection."
                    )

        quota_ftd = get_odds_api_quota()
        if quota_ftd["remaining"] is not None:
            st.caption(f"🔑 Odds API quota: {quota_ftd['remaining']:,} credits remaining this billing period.")

    # ---------------- Game Center (single-game breakdown) ----------------
    with tab_game:
        st.subheader("Single Game Breakdown")
        st.caption("Pick one game and see every tracked starter from both teams - handy on light slates like Thursday or Monday night.")

        schedule = get_schedule()
        schedule = schedule.copy()
        schedule["gameday_fmt"] = pd.to_datetime(schedule["gameday"]).dt.strftime("%a %-m/%-d")
        schedule["gametime_fmt"] = schedule["gametime"].apply(format_gametime) if "gametime" in schedule.columns else ""
        schedule["game_label"] = (
            schedule["gameday_fmt"] + (" " + schedule["gametime_fmt"]).where(schedule["gametime_fmt"] != "", "")
            + " — " + schedule["away_team"] + " @ " + schedule["home_team"]
        )

        upcoming = schedule[schedule["home_score"].isna()]
        default_week = int(upcoming["week"].min()) if not upcoming.empty else int(schedule["week"].max())

        weeks_available = sorted(schedule["week"].unique())
        # Session state is set (not just an `index=` default) so the Matchups
        # tab's "Open in Game Center" jump can pre-select a week/game by
        # writing to st.session_state before this widget runs - passing both
        # `index` and a pre-set session_state value causes a Streamlit warning,
        # so the default is seeded into session_state instead.
        if "game_week" not in st.session_state:
            st.session_state["game_week"] = default_week if default_week in weeks_available else weeks_available[0]
        game_week = st.selectbox("Week", weeks_available, key="game_week")

        week_games = schedule[schedule["week"] == game_week].sort_values("gameday")

        if week_games.empty:
            st.info("No games scheduled for this week.")
        else:
            game_labels = week_games["game_label"].tolist()
            if "game_pick" not in st.session_state or st.session_state["game_pick"] not in game_labels:
                st.session_state["game_pick"] = game_labels[0]
            game_label = st.selectbox("Game", game_labels, key="game_pick")
            game_row = week_games[week_games["game_label"] == game_label].iloc[0]

            away, home = game_row["away_team"], game_row["home_team"]
            # Own sort-stat control, independent of Overview's sidebar one -
            # Game Center now lives on the Prop Bets side, which can render
            # without Overview (on the Fantasy Lineups side) ever having run
            # this pass, so it can't rely on Overview having set a shared
            # variable. Same "pull every numeric stat automatically" logic.
            gc_sort_options = [
                c for c in current_season_df.columns
                if pd.api.types.is_numeric_dtype(current_season_df[c]) and c not in ("season", "week")
            ]
            if "fantasy_points_ppr" in gc_sort_options:
                gc_sort_options.remove("fantasy_points_ppr")
                gc_sort_options.insert(0, "fantasy_points_ppr")
            sort_stat = st.selectbox("Rank player cards by", gc_sort_options, index=0, key="game_center_sort_stat")

            gm1, gm2, gm3, gm4 = st.columns(4)
            gm1.metric("Matchup", f"{away} @ {home}")
            kickoff_full = format_kickoff(game_row.get("gameday"), game_row.get("gametime"))
            if kickoff_full:
                gm1.caption(f"Kickoff: {kickoff_full}")
            if pd.notna(game_row.get("spread_line")):
                fav = home if game_row["spread_line"] < 0 else away
                gm2.metric("Spread", f"{fav} {-abs(game_row['spread_line']):.1f}")
            if pd.notna(game_row.get("total_line")):
                gm3.metric("Total", f"{game_row['total_line']:.1f}")
                home_imp, away_imp = implied_totals(game_row.get("spread_line"), game_row["total_line"], home, away)
                if home_imp is not None:
                    gm3.caption(f"Implied: {away} {away_imp} — {home} {home_imp}")
            if pd.notna(game_row.get("temp")):
                # Actual recorded conditions - only present for games already played
                gm4.metric("Temp / Wind (actual)", f"{int(game_row['temp'])}°F / {int(game_row.get('wind', 0) or 0)} mph")
            else:
                forecast = weather_badge(home, game_row.get("gameday"), game_row.get("roof"), game_row.get("location"))
                if forecast:
                    forecast_text, forecast_risk = forecast
                    gm4.metric("Forecast", forecast_text)
                    gm4.markdown(weather_risk_badge_html("Weather risk", forecast_risk, tag="span"), unsafe_allow_html=True)
                elif pd.notna(game_row.get("roof")) and game_row["roof"] in INDOOR_ROOF_STATES:
                    gm4.metric("Conditions", "Indoors")

            defense_ranks = get_defense_ranks()

            def matchup_note(offense_team: str, defense_team: str) -> str:
                """One-line summary of how tough the opposing defense is against
                each position, for the team about to face them."""
                parts = []
                for pos in ["QB", "RB", "WR", "TE"]:
                    row = defense_ranks[(defense_ranks["position"] == pos) & (defense_ranks["team"] == defense_team)]
                    if not row.empty and "fantasy_points_ppr_rank" in row.columns:
                        parts.append(f"{pos} #{int(row['fantasy_points_ppr_rank'].iloc[0])}")
                return f"{defense_team} defense ranks: " + ", ".join(parts) if parts else ""

            col_away, col_home = st.columns(2)
            for col, team, opponent, home_away_label in [(col_away, away, home, "Away"), (col_home, home, away, "Home")]:
                with col:
                    st.markdown(
                        f"### {team_logo_html(team, px=44)}{team} "
                        f"<span style='font-size:15px; color:{theme.SUB}; font-weight:400;'>({home_away_label})</span>",
                        unsafe_allow_html=True,
                    )
                    note = matchup_note(team, opponent)
                    if note:
                        st.caption(note)

                    team_view = current_season_df[current_season_df["team"] == team]
                    team_summary = build_player_summary(team_view, sort_stat)
                    if team_summary.empty:
                        st.info("No tracked starters with data for this team yet.")
                    else:
                        # Large Hot-Picks-style cards here (not the compact
                        # Overview grid) - this is a per-team roster, a
                        # handful of players, not the "everyone at once"
                        # view Overview exists for.
                        render_hotpick_cards(
                            team_summary.to_dict("records"),
                            body_fn=lambda row: _summary_card_body(row, sort_stat),
                            cols_per_row=2, headshot_px=128,
                        )

    # ---------------- Bet Slip Tracker (OCR-assisted manual entry) ----------------
    with tab_slips:
        st.subheader("Bet Slip Tracker")
        cap_col, info_col = st.columns([5, 1])
        with cap_col:
            st.caption("Drop a bet-slip screenshot to pre-fill the form below, then review and save - builds a running history for performance tracking.")
        with info_col:
            theme.info_popover(
                "**How the auto-fill works:** the screenshot is read with local OCR (no API key, no cost, "
                "nothing uploaded anywhere) and matched against common sportsbook-app wording to guess the "
                "sportsbook, bet type, odds, stake, payout, and individual legs. OCR on stylized app "
                "screenshots is never perfect - always double-check the fields (and the raw text it found, "
                "in the expander) before saving. The image itself isn't kept; only what you save from the "
                "form below is stored.",
                label="ℹ️ How this works",
            )

        if slip_store.using_local_fallback(st.secrets):
            err = slip_store.last_connection_error()
            st.warning(
                "Google Sheets isn't configured yet, so bet slips are being saved to this app's local disk "
                "instead - that storage does NOT survive the next code deploy. See README to set up the "
                "Sheets connection before relying on this for real."
                + (f"\n\n**Reason:** `{err}`" if err else ""),
                icon="⚠️",
            )

        st.markdown("#### Add a bet slip")
        uploaded = st.file_uploader(
            "Drop a bet-slip screenshot (or click to browse)",
            type=["png", "jpg", "jpeg", "webp"], key="slip_upload",
        )

        parsed = {"sportsbook": "Other", "bet_type": "Single", "stake": 0.0, "potential_payout": 0.0, "odds": "", "legs": []}
        if uploaded is not None:
            with st.spinner("Reading the screenshot..."):
                ocr_text = slip_parser.ocr_image_to_text(uploaded.getvalue())
            if ocr_text.strip():
                parsed = slip_parser.parse_slip_text(ocr_text)
                st.success("Read the screenshot - check the fields below before saving (OCR isn't perfect).", icon="✅")
                with st.expander("Raw text OCR found (for double-checking)"):
                    st.text(ocr_text)
            else:
                st.warning("Couldn't read any text from that image - fill in the fields manually below.", icon="⚠️")

        with st.form("slip_entry_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                slip_date = st.date_input("Date placed", value=datetime.date.today())
                sb_index = slip_store.SPORTSBOOKS.index(parsed["sportsbook"]) if parsed["sportsbook"] in slip_store.SPORTSBOOKS else len(slip_store.SPORTSBOOKS) - 1
                sportsbook = st.selectbox("Sportsbook", slip_store.SPORTSBOOKS, index=sb_index)
            with c2:
                bt_index = slip_store.BET_TYPES.index(parsed["bet_type"]) if parsed["bet_type"] in slip_store.BET_TYPES else 0
                bet_type = st.selectbox("Bet type", slip_store.BET_TYPES, index=bt_index)
                odds = st.text_input("Odds (American, e.g. +450 or -110)", value=parsed["odds"])
            with c3:
                stake = st.number_input("Stake ($)", min_value=0.0, value=float(parsed["stake"]), step=1.0)
                potential_payout = st.number_input("Potential payout ($)", min_value=0.0, value=float(parsed["potential_payout"]), step=1.0)

            legs_text = st.text_area(
                "Legs (one per line)", value="\n".join(parsed["legs"]),
                placeholder="Josh Allen Over 249.5 Passing Yards\nCeeDee Lamb Anytime TD",
                height=120,
            )
            notes = st.text_input("Notes (optional)")
            submitted = st.form_submit_button("💾 Save slip", type="primary")
            if submitted:
                if stake <= 0:
                    st.error("Enter a stake before saving.")
                else:
                    new_slip = {
                        "id": str(uuid.uuid4()),
                        "date": slip_date.isoformat(),
                        "sportsbook": sportsbook,
                        "bet_type": bet_type,
                        "legs": [l.strip() for l in legs_text.splitlines() if l.strip()],
                        "odds": odds.strip(),
                        "stake": stake,
                        "potential_payout": potential_payout,
                        "result": "Pending",
                        "actual_payout": 0.0,
                        "notes": notes.strip(),
                    }
                    ok, msg = slip_store.save_slip(st.secrets, new_slip)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

        st.divider()
        st.subheader("Slip history & performance")

        slips = slip_store.load_slips(st.secrets)
        if not slips:
            st.info("No bet slips tracked yet - add your first one above.")
        else:
            hist_df = pd.DataFrame(slips)

            def _profit(row):
                if row["result"] == "Won":
                    return row["actual_payout"] - row["stake"]
                if row["result"] == "Lost":
                    return -row["stake"]
                if row["result"] == "Cashed Out":
                    return row["actual_payout"] - row["stake"]
                return 0.0  # Pending or Push

            hist_df["profit"] = hist_df.apply(_profit, axis=1)
            settled = hist_df[hist_df["result"].isin(["Won", "Lost"])]
            total_staked = hist_df["stake"].sum()
            total_profit = hist_df["profit"].sum()
            win_rate = (settled["result"] == "Won").mean() * 100 if not settled.empty else None
            roi = (total_profit / total_staked * 100) if total_staked else 0.0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total staked", f"${total_staked:,.2f}")
            m2.metric("Total profit/loss", f"${total_profit:,.2f}")
            m3.metric("Win rate", f"{win_rate:.0f}%" if win_rate is not None else "—")
            m3.caption(f"{len(settled)} settled bet(s)" if not settled.empty else "No settled bets yet")
            m4.metric("ROI", f"{roi:.1f}%")

            settled_sorted = hist_df[hist_df["result"].isin(["Won", "Lost", "Cashed Out"])].sort_values("date").copy()
            if not settled_sorted.empty:
                settled_sorted["cumulative_profit"] = settled_sorted["profit"].cumsum()
                profit_chart = alt.Chart(settled_sorted).mark_line(point=True, color=theme.ACCENT).encode(
                    x=alt.X("date:T", title="Date"),
                    y=alt.Y("cumulative_profit:Q", title="Cumulative profit ($)"),
                    tooltip=["date", "sportsbook", "result", alt.Tooltip("profit:Q", format="$.2f"), alt.Tooltip("cumulative_profit:Q", format="$.2f", title="Running total")],
                ).properties(height=240)
                st.altair_chart(profit_chart, use_container_width=True)

            st.markdown("#### Update results")
            st.caption("Change a slip's result (and actual payout, once it's settled) as bets play out, then save.")
            edit_df = hist_df[[
                "id", "date", "sportsbook", "bet_type", "legs", "odds",
                "stake", "potential_payout", "result", "actual_payout", "notes",
            ]].copy()
            edit_df["legs"] = edit_df["legs"].apply(lambda l: "; ".join(l) if isinstance(l, list) else "")

            edited = st.data_editor(
                edit_df,
                use_container_width=True, hide_index=True, key="slip_editor", row_height=40,
                column_order=["date", "sportsbook", "bet_type", "legs", "odds", "stake", "potential_payout", "result", "actual_payout", "notes"],
                column_config={
                    "id": st.column_config.TextColumn(disabled=True),
                    "date": st.column_config.TextColumn(disabled=True, width="small"),
                    "sportsbook": st.column_config.TextColumn(disabled=True, width="small"),
                    "bet_type": st.column_config.TextColumn(disabled=True, width="small"),
                    "legs": st.column_config.TextColumn(disabled=True, width=280),
                    "odds": st.column_config.TextColumn(disabled=True, width="small"),
                    "stake": st.column_config.NumberColumn(disabled=True, width="small", format="$%.2f"),
                    "potential_payout": st.column_config.NumberColumn(disabled=True, width="small", format="$%.2f", label="Potential"),
                    "result": st.column_config.SelectboxColumn(options=slip_store.RESULTS, width="small"),
                    "actual_payout": st.column_config.NumberColumn(width="small", format="$%.2f", label="Actual payout"),
                    "notes": st.column_config.TextColumn(disabled=True, width=180),
                },
            )

            if st.button("💾 Save result updates"):
                by_id = {s["id"]: s for s in slips}
                changed = 0
                for _, row in edited.iterrows():
                    original = by_id.get(row["id"])
                    if original is None:
                        continue
                    if row["result"] != original["result"] or float(row["actual_payout"]) != float(original["actual_payout"]):
                        original["result"] = row["result"]
                        original["actual_payout"] = float(row["actual_payout"])
                        slip_store.save_slip(st.secrets, original)
                        changed += 1
                if changed:
                    st.success(f"Updated {changed} slip(s).")
                    st.rerun()
                else:
                    st.info("No changes to save.")

            with st.expander("Delete a slip"):
                slip_labels = {s["id"]: f"{s['date']} · {s['sportsbook']} · {s['bet_type']} · ${s['stake']:.2f} · {s['result']}" for s in slips}
                del_id = st.selectbox("Slip", list(slip_labels.keys()), format_func=lambda i: slip_labels[i], key="slip_delete_pick")
                if st.button("🗑️ Delete this slip"):
                    slip_store.delete_slip(st.secrets, del_id)
                    st.success("Deleted.")
                    st.rerun()
elif tab_side == "🔥 Hot Picks":
    # ---------------- Hot Picks (league-wide scouting view) ----------------
    # One combined page, not sub-tabs - three curated leaderboards built
    # from the exact same live data (and the exact same helper functions:
    # compute_summary, _matchup_label, get_prop_lines, get_anytime_td_odds,
    # get_first_td_odds) as the rest of the site, so a "hot pick" here is
    # never a separate/stale computation from what the Overview or First TD
    # tabs would tell you about the same player.
    st.subheader("🔥 Hot Picks")
    st.caption(
        "A league-wide scouting view, refreshed from the same live data as the rest of the site: the biggest "
        "prop-line edges, the best touchdown-scoring chances, and the safest high-floor, most-consistent "
        "plays. Every number here is explained in more depth on its own tab elsewhere in the app."
    )
    theme.info_popover(
        f"**Confidence badges** are this page's continuous-improvement loop: every time it loads, it re-reads "
        f"Track Record's resolved (Hit/Miss) pick history and groups it by category + position - for example, "
        f"\"Prop Edges, RB\" or \"First TD, WR.\" Once a segment has **{CONFIDENCE_MIN_N}+ resolved picks**, it's "
        f"marked 🔥 **Hot** ({CONFIDENCE_HIGH_PCT:.0f}%+ hit rate), 🧊 **Cold** ({CONFIDENCE_LOW_PCT:.0f}% or below), "
        f"or ➖ **Even** (in between); fewer than {CONFIDENCE_MIN_N} resolved picks shows 🆕 **New** instead, since "
        f"a small sample isn't trustworthy yet. Hot segments are sorted toward the top of Prop-Line Edges and TD "
        f"Scoring Chances, cold segments toward the bottom - **nothing is ever hidden**, only re-ordered and "
        f"labeled. Safe Plays isn't tracked in Track Record (see that tab for why), so it has no confidence badge.",
        label="ℹ️ About Confidence badges",
    )
    theme.info_popover(
        "**📈 Implied Total badges** show a team's Vegas-implied point total for its game this week - the "
        "over/under split by the spread, a well-known handicapping proxy for how good an offensive environment "
        "a team is expected to be in. It's a second re-ranking signal alongside Confidence: a good environment "
        "(26+ implied points) floats a pick toward the top, a weak one (19 or below) sinks it, same **re-order, "
        "never hide** rule. A team on a bye or without a posted line yet just has no badge - that's a data gap, "
        "not a signal the environment is bad. Confidence (this segment's own history) is checked first; implied "
        "total only breaks ties within a confidence tier.",
        label="ℹ️ About Implied Total badges",
    )
    theme.info_popover(
        "**🎯 Fair Prob badges** (Prop-Line Edges only) show the sportsbook market's own de-vigged probability "
        "that this pick's specific side (Over or Under) hits - the book's raw Over/Under prices always sum to "
        "a bit over 100% because of the book's built-in margin, so this strips that margin back out first, the "
        "same way a professional handicapper reads a line. It's a second, independent opinion sitting next to "
        "our season-average-vs-line edge: our model can show a big edge on a side the market itself still sees "
        "as close to a coin flip (or vice versa), and that gap is worth knowing. Purely informational - it "
        "doesn't affect sorting, since it measures the market's confidence, not a track record. No badge means "
        "no bookmaker posted both sides' prices for that player/market this week.",
        label="ℹ️ About Fair Prob badges",
    )
    theme.info_popover(
        f"**📈/📉 Role trending badges** compare a player's opportunity metric (target share for receiving "
        f"stats, snap share otherwise) over their last {OPPORTUNITY_TREND_LAST_N} games to their own full-"
        f"{CURRENT_SEASON}-season average of that same metric - a flat season average can't show a role that's "
        f"changed recently, but this can. A badge only appears once the swing is {OPPORTUNITY_TREND_THRESHOLD_PP:.0f}+ "
        f"percentage points either way (smaller swings are shown as \"stable\" in the dataframe below, not "
        f"flagged as a card badge) and only once a player has played 2+ games this season - there's nothing to "
        f"compare against before that. Purely informational context for why a season average might be about to "
        f"catch up (or might already be stale), not a sort factor.",
        label="ℹ️ About Role Trending badges",
    )

    hp1, hp2 = st.columns(2)
    with hp1:
        hot_positions = st.multiselect(
            "Position", ["QB", "RB", "WR", "TE"], default=["QB", "RB", "WR", "TE"], key="hot_positions",
        )
    with hp2:
        hot_teams = st.multiselect("Team", sorted(current_season_df["team"].dropna().unique()), default=[], key="hot_teams")

    hp_defense_ranks = get_defense_ranks()
    hp_schedule = get_schedule()
    hp_next_opp_map = build_next_opponent_map(hp_schedule)
    hp_prop_lines = get_prop_lines()
    hp_anytime_td = get_anytime_td_odds()
    hp_first_td = get_first_td_odds()
    hp_injuries = get_injuries()
    hp_snap_counts = get_snap_counts()
    # Continuous-improvement loop: re-read Track Record's resolved picks
    # live on every page load (automatic rollout, per the user's choice) and
    # bucket hit rate by (category, position). Used below to re-SORT and
    # BADGE Sections 1/2 and Suggested Bets - never to filter/hide anything.
    hp_segment_confidence = compute_segment_confidence(pick_tracker_store.load_picks(st.secrets))
    # Vegas-implied team totals for this week (see build_team_implied_totals'
    # docstring for the spread/total math) - a second re-ranking signal
    # alongside confidence, this one about THIS week's specific game
    # environment rather than historical hit rate. Same "badge + re-sort,
    # never hide" treatment.
    hp_implied_totals = build_team_implied_totals(hp_schedule)

    edge_rows = []
    td_rows = []
    matchup_rows = []

    # Deliberately iterates the FULL player universe (current_season_df),
    # not a position/team-filtered view - the position/team multiselects
    # above are a display filter only, applied to edge_rows/td_rows/
    # matchup_rows further down, AFTER the tracking snapshot below. If
    # this loop itself were scoped to the UI filter, a session left
    # narrowed to (say) just RBs would silently stop tracking every other
    # position's picks - the snapshot needs the same full set every week
    # regardless of whatever filter happens to be selected when the page
    # loads.
    for player, pdf in current_season_df.groupby("player"):
        first_row = pdf.iloc[0]
        team = first_row["team"]
        position = first_row["position"]
        player_id = first_row.get("player_id", "")
        headshot_url = first_row.get("headshot_url")
        team_color = first_row.get("team_color") or "#444444"

        # Skip players who are ruled Out this week for every section - a
        # "hot pick" that literally can't take the field isn't useful,
        # whichever of the three lists it would otherwise land on.
        inj_row = hp_injuries[hp_injuries["player"] == player] if not hp_injuries.empty else pd.DataFrame()
        if not inj_row.empty and inj_row["report_status"].iloc[0] == "Out":
            continue

        # ---- Biggest prop-line edge: check every stat this position has a
        # live market for, keep whichever one has the largest |avg - line|
        # (one row per player, not one row per stat, so a player with
        # several tracked markets doesn't crowd out everyone else). ----
        best_edge = None
        for stat in PROP_STATS_BY_POSITION.get(position, []):
            market = PROP_MARKET_MAP.get(stat)
            if not market or stat not in pdf.columns or hp_prop_lines.empty:
                continue
            match = hp_prop_lines[(hp_prop_lines["player"] == player) & (hp_prop_lines["market"] == market)]
            if match.empty:
                continue
            avg_val = pdf[stat].mean()
            line_val = float(match["point"].iloc[0])
            delta = avg_val - line_val
            # fair_prob_over is de-vigged Over probability (0-1), added to
            # load_prop_lines' output later than "point" - a cache file
            # written before that change won't have the column at all, so
            # this checks for its presence rather than assuming it, and
            # falls back to None (unknown) the same way a missing/NaN
            # value would - never treated as 0%.
            fair_prob_over = None
            if "fair_prob_over" in match.columns:
                raw_fpo = match["fair_prob_over"].iloc[0]
                if pd.notna(raw_fpo):
                    fair_prob_over = float(raw_fpo)
            if best_edge is None or abs(delta) > abs(best_edge["delta"]):
                best_edge = {"stat": stat, "avg": avg_val, "line": line_val, "delta": delta, "fair_prob_over": fair_prob_over}
        if best_edge:
            edge_rows.append({
                "player": player, "player_id": player_id, "team": team, "position": position,
                "headshot_url": headshot_url, "team_color": team_color,
                "stat": best_edge["stat"].replace("_", " ").title(),
                "stat_col": best_edge["stat"],
                "season_avg": round(best_edge["avg"], 1),
                "prop_line": round(best_edge["line"], 1),
                "edge": round(best_edge["delta"], 1),
                "direction": "▲ Over" if best_edge["delta"] > 0 else "▼ Under",
                "implied_total": hp_implied_totals.get(team),
                "fair_prob_over": best_edge["fair_prob_over"],
                "opportunity_trend": compute_opportunity_trend(stats_df, hp_snap_counts, player, best_edge["stat"], CURRENT_SEASON),
            })

        # ---- Best TD scoring chances: anytime + first TD side by side ----
        anytime_match = hp_anytime_td[hp_anytime_td["player"] == player] if not hp_anytime_td.empty else pd.DataFrame()
        first_match = hp_first_td[hp_first_td["player"] == player] if not hp_first_td.empty else pd.DataFrame()
        anytime_pct = float(anytime_match["implied_prob"].iloc[0]) if not anytime_match.empty else None
        first_pct = float(first_match["implied_prob"].iloc[0]) if not first_match.empty else None
        if anytime_pct is not None or first_pct is not None:
            # TD scoring chances aren't tied to one specific stat the way
            # a prop-line edge is - use receiving opportunity (target
            # share) for the positions that mostly score through the air,
            # snap share (compute_opportunity_trend's fallback) for
            # everyone else, as the best available role-trend proxy.
            td_trend_stat = "receiving_yards" if position in ("WR", "TE") else "rushing_yards"
            td_rows.append({
                "player": player, "player_id": player_id, "team": team, "position": position,
                "headshot_url": headshot_url, "team_color": team_color,
                "anytime_td_pct": anytime_pct, "first_td_pct": first_pct,
                "implied_total": hp_implied_totals.get(team),
                "opportunity_trend": compute_opportunity_trend(stats_df, hp_snap_counts, player, td_trend_stat, CURRENT_SEASON),
            })

        # ---- Safe plays: High consistency + an upcoming game ----
        # Matchup difficulty is shown on the card as CONTEXT but no longer
        # gates or ranks this list - a walk-forward backtest against the
        # real 2025 season tested it every way (as the primary sort, as a
        # stricter cutoff at various thresholds, as a "no really bad
        # matchup" exclusion filter) and it never separated winners from
        # losers by more than noise. Consistency itself is where the real,
        # backtest-confirmed edge lives (see CONSISTENCY_HIGH_CV's
        # docstring), so that's now both the filter AND the sort key -
        # most consistent player first, not easiest matchup first.
        avg_fp, _, _, consistency, cv = compute_summary(pdf, "fantasy_points_ppr")
        # plain=True: this lands in a st.dataframe cell below, which shows
        # HTML source literally instead of rendering it (same reasoning as
        # add_matchup_display's separate plain-text column elsewhere).
        matchup_result = _matchup_label(team, position, "fantasy_points_ppr", hp_next_opp_map, hp_defense_ranks, plain=True)
        if consistency == "High" and matchup_result:
            matchup_label, matchup_rank = matchup_result
            safe_trend_stat = "receiving_yards" if position in ("WR", "TE") else "rushing_yards"
            matchup_rows.append({
                "player": player, "player_id": player_id, "team": team, "position": position,
                "headshot_url": headshot_url, "team_color": team_color,
                "matchup_label": matchup_label, "matchup_rank": matchup_rank, "cv": cv,
                "season_avg_ppr": round(avg_fp, 1),
                "implied_total": hp_implied_totals.get(team),
                # A "High consistency" verdict is itself a look backward
                # over the whole season - if the role behind that
                # consistency is now trending down, that's exactly the
                # kind of thing worth flagging on a "safe" pick.
                "opportunity_trend": compute_opportunity_trend(stats_df, hp_snap_counts, player, safe_trend_stat, CURRENT_SEASON),
            })

    # Snapshot the full, unfiltered set for the Track Record tab BEFORE
    # applying the position/team display filter below - see the loop's
    # comment above for why. A no-op after the first page load of the
    # week (season_week_already_tracked short-circuits it).
    snapshot_hotpicks_for_tracking(edge_rows, td_rows, get_schedule())

    # Opportunistic Closing Line Value capture - re-stamps every still-
    # Pending pick's "closing" line/probability from this same page
    # load's already-pulled live odds, as long as its game hasn't kicked
    # off yet. Runs every load (not once-per-week like the snapshot
    # above) since the whole point is catching the line as it moves
    # throughout the week - see update_closing_lines' docstring.
    update_closing_lines(
        pick_tracker_store.load_picks(st.secrets), hp_prop_lines, hp_anytime_td, hp_first_td, hp_schedule,
    )

    # NOW apply the position/team display filter (everything above this
    # point used the full, unfiltered player universe on purpose). An
    # empty multiselect means "no filter" here - same convention as the
    # First TD tab's position filter and this page's own team filter
    # just below, not "show nothing."
    if hot_positions:
        edge_rows = [r for r in edge_rows if r["position"] in hot_positions]
        td_rows = [r for r in td_rows if r["position"] in hot_positions]
        matchup_rows = [r for r in matchup_rows if r["position"] in hot_positions]
    if hot_teams:
        edge_rows = [r for r in edge_rows if r["team"] in hot_teams]
        td_rows = [r for r in td_rows if r["team"] in hot_teams]
        matchup_rows = [r for r in matchup_rows if r["team"] in hot_teams]

    section1, section2, section3 = st.columns(3)
    section1.metric("Prop Edges Found", len(edge_rows))
    section2.metric("TD Chances Tracked", len(td_rows))
    section3.metric("High-Consistency Safe Plays", len(matchup_rows))

    # ---- Position mix donut: the one place on this page a donut earns its
    # keep - a real part-to-whole with only 4 possible slices (QB/RB/WR/TE),
    # not a close-values comparison a bar would read better. Every other
    # chart on this page is a magnitude comparison (biggest edge, highest
    # odds, best matchup), which a bar chart shows more precisely than a
    # pie ever could - so this is deliberately the only donut here.
    #
    # Clickable: an Altair point selection on the "position" field, wired
    # through Streamlit's on_select="rerun", turns a click into a rerun with
    # the clicked position(s) in donut_event.selection["pos_click"]. That
    # drives the "Suggested Bets" panel just below, which pulls together
    # whatever this player shows up as across the three sections (prop
    # edge / TD chances / safe play) rather than a separate computation -
    # shift/cmd-click adds another slice, clicking a selected slice again
    # clears it (toggle=True), and clicking empty space clears the whole
    # selection (empty=True treats "nothing selected" as "show everything"
    # on the donut itself, which is why the opacity dims only once
    # something IS selected). ----
    edge_lookup = {r["player"]: r for r in edge_rows}
    td_lookup = {r["player"]: r for r in td_rows}
    matchup_lookup = {r["player"]: r for r in matchup_rows}

    all_hot_players = {}
    for row in edge_rows + td_rows + matchup_rows:
        all_hot_players[row["player"]] = row["position"]
    selected_positions: list[str] = []
    if all_hot_players:
        position_counts = pd.Series(list(all_hot_players.values())).value_counts().reset_index()
        position_counts.columns = ["position", "count"]

        pos_click = alt.selection_point(fields=["position"], name="pos_click", toggle=True, empty=True)

        donut = alt.Chart(position_counts).mark_arc(innerRadius=70, cornerRadius=3, padAngle=0.015).add_params(pos_click).encode(
            theta=alt.Theta("count:Q", stack=True),
            color=alt.Color(
                "position:N", title="Position",
                scale=alt.Scale(domain=list(POSITION_COLORS.keys()), range=list(POSITION_COLORS.values())),
                legend=alt.Legend(orient="right"),
            ),
            opacity=alt.condition(pos_click, alt.value(1), alt.value(0.35)),
            order=alt.Order("position:N", sort="ascending"),
            tooltip=[
                alt.Tooltip("position:N", title="Position"),
                alt.Tooltip("count:Q", title="Hot picks"),
            ],
        ).properties(height=220)
        donut_labels = alt.Chart(position_counts).mark_text(radius=95, size=13, fontWeight=600).encode(
            theta=alt.Theta("count:Q", stack=True),
            order=alt.Order("position:N", sort="ascending"),
            text="count:Q",
            color=alt.value(theme.INK),
        )
        dcol1, dcol2 = st.columns([1, 2])
        with dcol1:
            st.markdown("###### This week's Hot Picks by position")
            donut_event = st.altair_chart(
                donut + donut_labels,
                use_container_width=True,
                on_select="rerun",
                key="hotpicks_donut",
            )
        with dcol2:
            st.markdown("###### ")
            st.caption(
                f"{len(all_hot_players)} unique players appear in at least one list below - "
                "the mix shows whether this week's hot picks skew toward a particular position. "
                "**Click a slice** to see that position's suggested bets (shift-click to add "
                "another, click it again to clear)."
            )

        if donut_event and donut_event.selection:
            selected_positions = sorted({
                point["position"] for point in donut_event.selection.get("pos_click", [])
                if "position" in point
            })

    if selected_positions:
        st.markdown(f"###### 🎯 Suggested Bets — {', '.join(selected_positions)}")
        suggestion_rows = []
        for player, position in all_hot_players.items():
            if position not in selected_positions:
                continue
            edge = edge_lookup.get(player)
            td = td_lookup.get(player)
            matchup = matchup_lookup.get(player)
            source = edge or td or matchup
            if not source:
                continue

            badges_html = ""
            if edge:
                delta_cls = "delta-up" if edge["direction"] == "▲ Over" else "delta-down"
                badges_html += (
                    f'<div style="margin-top:6px;"><span class="{delta_cls}">{edge["direction"]} '
                    f'{edge["prop_line"]:.1f} {edge["stat"]}</span> '
                    f'<span class="stat-label">({CURRENT_SEASON} avg {edge["season_avg"]:.1f}, '
                    f'edge {edge["edge"]:+.1f})</span></div>'
                )
                badges_html += fair_prob_badge_html(edge.get("fair_prob_over"), edge["direction"])
            if td:
                if td["anytime_td_pct"] is not None:
                    badges_html += anytime_td_badge_html(td["anytime_td_pct"])
                if td["first_td_pct"] is not None:
                    badges_html += first_td_badge_html(td["first_td_pct"])
            if matchup:
                badges_html += matchup_badge_html(matchup["matchup_label"], matchup["matchup_rank"])
                badges_html += f'<div class="stat-label" style="margin-top:4px;">{matchup["season_avg_ppr"]:.1f} avg PPR</div>'
            if not badges_html:
                badges_html = '<div class="stat-label" style="margin-top:8px;">No specific angle this week</div>'

            # Confidence badge: edge's segment takes priority (it's the
            # more specific claim - an exact stat line vs. a TD market),
            # then TD's driving category, then none for a matchup-only row
            # (Safe Plays isn't tracked in Track Record, so there's no
            # segment to look up). Informational only - this panel keeps
            # its existing alphabetical (position, player) sort below.
            conf_segment = None
            if edge:
                conf_segment = hp_segment_confidence.get(("edge", position))
            elif td:
                driving = "td_first" if (td["first_td_pct"] or -1) >= (td["anytime_td_pct"] or -1) else "td_anytime"
                conf_segment = hp_segment_confidence.get((driving, position))
            if edge or td:
                badges_html += confidence_badge_html(conf_segment)
            badges_html += implied_total_badge_html(hp_implied_totals.get(source["team"]))
            trend_source = edge or td
            if trend_source:
                badges_html += opportunity_trend_badge_html(trend_source.get("opportunity_trend"))

            suggestion_rows.append({
                "player": player,
                "position": position,
                "team": source["team"],
                "headshot_url": source.get("headshot_url"),
                "team_color": source.get("team_color"),
                "badges_html": badges_html,
            })

        if suggestion_rows:
            suggestion_rows.sort(key=lambda r: (r["position"], r["player"]))
            render_hotpick_cards(
                suggestion_rows,
                body_fn=lambda row: row["badges_html"],
                cols_per_row=4,
                headshot_px=128,
            )
        else:
            st.info("No suggested bets found for that position this week.")

    st.divider()

    # ---- Section 1: Biggest Prop-Line Edges ----
    st.markdown("##### 📈 Biggest Prop-Line Edges")
    st.caption("Season average vs. the live sportsbook line, for whichever tracked stat shows the biggest gap for that player.")
    if not edge_rows:
        st.info("No live prop lines available right now to compare against - try \"Refresh prop lines now\" at the top of the page.")
    else:
        edge_df = pd.DataFrame(edge_rows)
        edge_df["_abs_edge"] = edge_df["edge"].abs()
        edge_df["confidence"] = edge_df["position"].apply(lambda pos: hp_segment_confidence.get(("edge", pos)))
        edge_df["_conf_rank"] = edge_df["confidence"].apply(
            lambda seg: CONFIDENCE_TIER_RANK[seg["tier"]] if seg else CONFIDENCE_TIER_RANK["new"]
        )
        edge_df["_env_rank"] = edge_df["implied_total"].apply(lambda t: IMPLIED_TOTAL_TIER_RANK[implied_total_tier(t)])
        # Confidence tier first (hot/cold segments float/sink based on
        # actual Track Record history), then this week's Vegas-implied
        # scoring environment (a good environment floats up, a bad one
        # sinks), then the edge size itself breaks ties within both tiers
        # - re-ranks, never filters, same "insights + confidence
        # weighting, never hide anything" choice the confidence loop used.
        edge_df = edge_df.sort_values(["_conf_rank", "_env_rank", "_abs_edge"], ascending=[True, True, False]).head(15)

        st.markdown("###### This week's biggest edges")
        edge_card_rows = edge_df.head(3).to_dict("records")

        def _edge_card_body(row: dict) -> str:
            # Single-line HTML, no embedded newlines - matching every other
            # HTML-returning helper in this file (pill_badge_html, delta_html,
            # etc.). A multi-line triple-quoted string here breaks Streamlit's
            # markdown-it HTML-block parsing when it's spliced into the
            # outer card template (a blank/whitespace-only line acts as an
            # HTML-block terminator), which showed up as a literal, visible
            # "</div>" on the card instead of a closed tag - caught by
            # actually rendering this with populated data before shipping.
            delta_cls = "delta-up" if row["direction"] == "▲ Over" else "delta-down"
            return (
                f'<div class="stat-big">{row["edge"]:+.1f}</div>'
                f'<div class="stat-label">{row["stat"]} edge</div>'
                f'<div style="margin-top:6px;"><span class="{delta_cls}">{row["direction"]} '
                f'{row["prop_line"]:.1f} (avg {row["season_avg"]:.1f})</span></div>'
                f'{confidence_badge_html(row.get("confidence"))}'
                f'{implied_total_badge_html(row.get("implied_total"))}'
                f'{fair_prob_badge_html(row.get("fair_prob_over"), row["direction"])}'
                f'{opportunity_trend_badge_html(row.get("opportunity_trend"))}'
            )

        render_hotpick_cards(edge_card_rows, body_fn=_edge_card_body, cols_per_row=3, headshot_px=128, medals=True)

        # Diverging bar around 0 (positive = Over, negative = Under) - the
        # right form for "above/below a baseline" per the site's charting
        # guide, and more precise than a donut for comparing edge sizes
        # that are often close to each other.
        edge_chart_df = edge_df.head(10).copy()
        edge_chart_df["label"] = edge_chart_df["player"] + " — " + edge_chart_df["stat"]
        # edge_df is already sorted by |edge| descending (line ~2279), so the
        # bar order just needs to follow that same label order - a plain list
        # sort, rather than alt.EncodingSortField(op="abs"), which Vega-Lite's
        # schema rejects (op only accepts real aggregation ops like "sum"/
        # "mean", not "abs"; this raised a live SchemaValidationError once real
        # prop-line data populated this chart).
        edge_label_order = edge_chart_df["label"].tolist()
        edge_bar = alt.Chart(edge_chart_df).mark_bar(cornerRadiusEnd=4, size=18).encode(
            x=alt.X("edge:Q", title="Edge (season avg − prop line)"),
            y=alt.Y("label:N", sort=edge_label_order, title=None),
            color=alt.Color(
                "direction:N", title=None,
                scale=alt.Scale(domain=["▲ Over", "▼ Under"], range=[theme.GOOD, theme.BAD]),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("player:N", title="Player"), alt.Tooltip("stat:N", title="Stat"),
                alt.Tooltip("season_avg:Q", title=f"{CURRENT_SEASON} Avg", format=".1f"),
                alt.Tooltip("prop_line:Q", title="Prop Line", format=".1f"),
                alt.Tooltip("edge:Q", title="Edge", format="+.1f"),
            ],
        )
        edge_text_pos = edge_bar.mark_text(
            align="left", dx=4, fontWeight=600,
        ).encode(
            text=alt.Text("edge:Q", format="+.1f"),
            color=alt.value(theme.INK),
        ).transform_filter(alt.datum.edge > 0)
        edge_text_neg = edge_bar.mark_text(
            align="right", dx=-4, fontWeight=600,
        ).encode(
            text=alt.Text("edge:Q", format="+.1f"),
            color=alt.value(theme.INK),
        ).transform_filter(alt.datum.edge < 0)
        st.altair_chart(
            (edge_bar + edge_text_pos + edge_text_neg).properties(height=max(220, 28 * len(edge_chart_df))),
            use_container_width=True,
        )

        edge_df["Confidence"] = edge_df["confidence"].apply(_confidence_label_text)
        edge_df["Implied Total"] = edge_df["implied_total"].apply(lambda t: f"{t:.1f}" if pd.notna(t) else "—")
        edge_df["Fair Prob"] = edge_df.apply(
            lambda r: (f"{(r['fair_prob_over'] if r['direction'] == '▲ Over' else 1.0 - r['fair_prob_over']) * 100:.0f}%"
                       if pd.notna(r.get("fair_prob_over")) else "—"),
            axis=1,
        )
        edge_df["Opportunity"] = edge_df["opportunity_trend"].apply(opportunity_trend_text)
        edge_display = edge_df[["player", "team", "position", "stat", "season_avg", "prop_line", "edge", "direction", "Confidence", "Implied Total", "Fair Prob", "Opportunity"]].rename(columns={
            "player": "Player", "team": "Team", "position": "Pos", "stat": "Stat",
            "season_avg": f"{CURRENT_SEASON} Avg", "prop_line": "Prop Line", "edge": "Edge", "direction": "Direction",
        })
        st.dataframe(
            edge_display, width="content", hide_index=True, row_height=38,
            column_config={"Edge": st.column_config.NumberColumn(format="%+.1f")},
        )

    st.divider()

    # ---- Section 2: Best TD Scoring Chances ----
    st.markdown("##### 🎯 Best TD Scoring Chances")
    st.caption("Market-implied probability (includes the sportsbook's margin) of scoring any touchdown, and specifically the first one, this week.")
    if not td_rows:
        st.info("No live TD odds available right now - try \"Refresh prop lines now\" at the top of the page.")
    else:
        td_df = pd.DataFrame(td_rows)
        td_df["_sort"] = td_df[["anytime_td_pct", "first_td_pct"]].max(axis=1, skipna=True)
        # Driving category = whichever of anytime/first TD is this row's
        # larger (displayed) number - same comparison _sort already makes -
        # since that's the market the card is actually spotlighting. Uses
        # pd.notna rather than a plain "or" fallback because a missing
        # value here is NaN (not None) once it's in a DataFrame column,
        # and NaN is truthy in Python - "x or -1" would silently keep the
        # NaN instead of falling back, breaking the >= comparison.
        def _td_driving_category(r):
            anytime = r["anytime_td_pct"] if pd.notna(r["anytime_td_pct"]) else -1
            first = r["first_td_pct"] if pd.notna(r["first_td_pct"]) else -1
            return "td_first" if first >= anytime else "td_anytime"

        td_df["_driving_category"] = td_df.apply(_td_driving_category, axis=1)
        td_df["confidence"] = td_df.apply(
            lambda r: hp_segment_confidence.get((r["_driving_category"], r["position"])), axis=1,
        )
        td_df["_conf_rank"] = td_df["confidence"].apply(
            lambda seg: CONFIDENCE_TIER_RANK[seg["tier"]] if seg else CONFIDENCE_TIER_RANK["new"]
        )
        # Lighter touch than Section 1's env_rank tiebreaker: a player's own
        # TD odds are already priced off their team's Vegas total to some
        # degree, so implied total is a smaller marginal signal here than
        # it is for a stat edge measured against a flat season average -
        # still included as the same tiered tiebreaker for a consistent,
        # explainable rule across both sections.
        td_df["_env_rank"] = td_df["implied_total"].apply(lambda t: IMPLIED_TOTAL_TIER_RANK[implied_total_tier(t)])
        td_df = td_df.sort_values(["_conf_rank", "_env_rank", "_sort"], ascending=[True, True, False]).head(15)

        st.markdown("###### This week's best scoring chances")
        td_card_rows = td_df.head(3).to_dict("records")

        def _td_card_body(row: dict) -> str:
            badges = ""
            if pd.notna(row.get("anytime_td_pct")):
                badges += anytime_td_badge_html(row["anytime_td_pct"])
            if pd.notna(row.get("first_td_pct")):
                badges += first_td_badge_html(row["first_td_pct"])
            badges += confidence_badge_html(row.get("confidence"))
            badges += implied_total_badge_html(row.get("implied_total"))
            badges += opportunity_trend_badge_html(row.get("opportunity_trend"))
            return badges

        render_hotpick_cards(td_card_rows, body_fn=_td_card_body, cols_per_row=3, headshot_px=128, medals=True)

        td_long = pd.concat([
            td_df[["player", "anytime_td_pct"]].rename(columns={"anytime_td_pct": "pct"}).assign(market="🎯 Anytime TD"),
            td_df[["player", "first_td_pct"]].rename(columns={"first_td_pct": "pct"}).assign(market="🥇 First TD"),
        ]).dropna(subset=["pct"])
        td_chart_df = td_long[td_long["player"].isin(td_df.head(10)["player"])]
        td_bar = alt.Chart(td_chart_df).mark_bar(cornerRadiusEnd=4).encode(
            x=alt.X("pct:Q", title="Implied probability", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("player:N", sort=td_df.head(10)["player"].tolist(), title=None),
            color=alt.Color(
                "market:N", title=None,
                scale=alt.Scale(domain=["🎯 Anytime TD", "🥇 First TD"], range=[theme.CATEGORY_COLORS["Anytime TD"], theme.CATEGORY_COLORS["First TD"]]),
                legend=alt.Legend(orient="top"),
            ),
            yOffset="market:N",
            tooltip=[alt.Tooltip("player:N", title="Player"), alt.Tooltip("market:N", title="Market"), alt.Tooltip("pct:Q", title="Chance", format=".0f")],
        ).properties(height=max(220, 22 * td_chart_df["player"].nunique() * 2))
        st.altair_chart(td_bar, use_container_width=True)

        td_df["Confidence"] = td_df["confidence"].apply(_confidence_label_text)
        td_df["Implied Total"] = td_df["implied_total"].apply(lambda t: f"{t:.1f}" if pd.notna(t) else "—")
        td_df["Opportunity"] = td_df["opportunity_trend"].apply(opportunity_trend_text)
        td_display = td_df[["player", "team", "position", "anytime_td_pct", "first_td_pct", "Confidence", "Implied Total", "Opportunity"]].rename(columns={
            "player": "Player", "team": "Team", "position": "Pos",
            "anytime_td_pct": "Anytime TD %", "first_td_pct": "First TD %",
        })
        st.dataframe(
            td_display, width="content", hide_index=True, row_height=38,
            column_config={
                "Anytime TD %": st.column_config.NumberColumn(format="%.0f%%"),
                "First TD %": st.column_config.NumberColumn(format="%.0f%%"),
            },
        )

    st.divider()

    # ---- Section 3: Safe Plays (High Consistency, ranked by consistency) ----
    st.markdown("##### 🛡️ Safe Plays — High Consistency")
    st.caption(
        f"Reliable, low-variance scorers (season coefficient of variation under {CONSISTENCY_HIGH_CV:.0%}, "
        f"{MIN_GAMES_FOR_CONSISTENCY}+ games played), ranked most-consistent first. Matchup is shown for "
        f"context, not as a ranking factor - a 2025 season backtest found it didn't predict anything here."
    )
    if not matchup_rows:
        st.info("No players currently have a High consistency rating with an upcoming game.")
    else:
        matchup_df = pd.DataFrame(matchup_rows).sort_values("cv", ascending=True).head(15)

        st.markdown("###### This week's safest plays")
        safe_card_rows = matchup_df.head(3).to_dict("records")

        def _safe_card_body(row: dict) -> str:
            # Single-line HTML - see _edge_card_body's comment above for why.
            badge = matchup_badge_html(row["matchup_label"], row["matchup_rank"]) if row.get("matchup_label") else ""
            return (
                f'<div class="stat-big">{row["season_avg_ppr"]:.1f}</div>'
                f'<div class="stat-label">avg PPR</div>'
                f'<div style="margin-top:2px;">{badge}</div>'
                f'{implied_total_badge_html(row.get("implied_total"))}'
                f'{opportunity_trend_badge_html(row.get("opportunity_trend"))}'
            )

        render_hotpick_cards(safe_card_rows, body_fn=_safe_card_body, cols_per_row=3, headshot_px=128, medals=True)

        safe_chart_df = matchup_df.head(10)
        safe_bar = alt.Chart(safe_chart_df).mark_bar(cornerRadiusEnd=4).encode(
            x=alt.X("season_avg_ppr:Q", title=f"{CURRENT_SEASON} Avg Fantasy Points (PPR)"),
            y=alt.Y("player:N", sort="-x", title=None),
            color=alt.Color(
                "position:N", title="Position",
                scale=alt.Scale(domain=list(POSITION_COLORS.keys()), range=list(POSITION_COLORS.values())),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("player:N", title="Player"), alt.Tooltip("team:N", title="Team"),
                alt.Tooltip("matchup_label:N", title="Matchup"),
                alt.Tooltip("season_avg_ppr:Q", title=f"{CURRENT_SEASON} Avg PPR", format=".1f"),
            ],
        ).properties(height=max(220, 28 * len(safe_chart_df)))
        st.altair_chart(safe_bar, use_container_width=True)

        matchup_df["Implied Total"] = matchup_df["implied_total"].apply(lambda t: f"{t:.1f}" if pd.notna(t) else "—")
        matchup_df["Opportunity"] = matchup_df["opportunity_trend"].apply(opportunity_trend_text)
        matchup_df["Consistency (CV)"] = matchup_df["cv"].apply(lambda c: f"{c:.2f}" if pd.notna(c) else "—")
        matchup_display = matchup_df[["player", "team", "position", "Consistency (CV)", "matchup_label", "season_avg_ppr", "Implied Total", "Opportunity"]].rename(columns={
            "player": "Player", "team": "Team", "position": "Pos",
            "matchup_label": "Matchup", "season_avg_ppr": f"{CURRENT_SEASON} Avg PPR",
        })
        st.dataframe(matchup_display, width="content", hide_index=True, row_height=38)

    st.caption(
        "All percentages and lines are live betting-market data, including the sportsbook's margin - not Prop "
        "Shop projections. \"Edge\" and \"Matchup\" figures reuse the exact same calculations as the Overview, "
        "Prop Comparator, and First TD tabs."
    )

else:
    # ---------------- Track Record (hit-rate tracking) ----------------
    # Every Prop-Line Edge and TD Chance pick Hot Picks surfaces gets
    # snapshotted once a week (snapshot_hotpicks_for_tracking, called
    # from the Hot Picks branch above) and checked against what actually
    # happened once each game is final (resolve_pending_picks, above).
    # This page is just the read side: hit-rate stats and history over
    # whatever's been snapshotted and resolved so far.
    st.subheader("📊 Track Record")
    st.caption(
        "Every Prop-Line Edge and TD Chance pick the Hot Picks page has surfaced gets saved automatically "
        "the first time that week's page loads, then checked against what actually happened once each "
        "game goes final. Safe Plays aren't tracked here - \"high consistency\" is a "
        "different kind of claim than a specific Over/Under or scoring prediction, so there's no single "
        "hit/miss to score it against."
    )

    if st.button("🔄 Check results now"):
        resolved, still_pending = resolve_pending_picks()
        if resolved:
            st.success(f"Resolved {resolved} pick(s) against final scores. {still_pending} still waiting on a final score.")
        elif still_pending:
            st.info(f"Nothing newly final yet - {still_pending} pick(s) still waiting on a final score.")
        else:
            st.info("No pending picks to check.")
        st.rerun()

    all_picks = pick_tracker_store.load_picks(st.secrets)

    if not all_picks:
        st.info("No picks tracked yet - check back after the Hot Picks page has loaded at least once this week.")
    else:
        resolved_picks = [p for p in all_picks if p["status"] != "Pending"]
        pending_count = len(all_picks) - len(resolved_picks)

        edge_pct, edge_hits, edge_n = compute_hit_rate(resolved_picks, "edge")
        any_pct, any_hits, any_n = compute_hit_rate(resolved_picks, "td_anytime")
        first_pct, first_hits, first_n = compute_hit_rate(resolved_picks, "td_first")

        category_labels = {"edge": "Prop Edge", "td_anytime": "Anytime TD", "td_first": "First TD"}
        category_order = ["Prop Edge", "Anytime TD", "First TD"]
        # Fixed-order categorical colors for the 3 tracked pick categories,
        # reusing 3 of the 4 already-validated colorblind-safe hues from
        # POSITION_COLORS (see that dict's own comment) rather than picking
        # new ones - these never share a chart with positions, so reuse is
        # safe, and it keeps this page visually related to Hot Picks
        # without literally reusing the anytime/first-TD pair (ACCENT vs
        # WARN), which fails the palette validator's CVD-separation check.
        category_colors = theme.CATEGORY_COLORS
        # Player headshot + team color/logo lookups, same source as every
        # other card on the site (get_meta merges rosters + team colors by
        # player name; team_logos is the module-level team_abbr -> logo
        # Series built once near team_logo_html). Tracked picks only store
        # player/team/position, not display info, so this join is what
        # lets the History table and Most Recent Picks cards show photos.
        tr_meta = get_meta().drop_duplicates(subset="player").set_index("player")

        # Clicking a tile filters the History table below to just that
        # category (Pending instead sets the Result filter to Pending
        # only) - a second click on the SAME tile clears it. This has to
        # go through session_state in an on_click callback, not a plain
        # "if st.button(...):" check, for the same reason jump_to_game_center
        # and _home_jump do: the History table's "Result" multiselect
        # (key="tr_result_filter") gets instantiated further down in this
        # same script pass, and reassigning an already-instantiated
        # widget's session_state key mid-script raises
        # StreamlitWidgetAlreadyInstantiatedError. A callback runs BEFORE
        # the next rerun, so it's always safe.
        def _toggle_category_filter(category: str) -> None:
            current = st.session_state.get("tr_category_filter")
            st.session_state["tr_category_filter"] = None if current == category else category

        def _filter_to_pending() -> None:
            st.session_state["tr_category_filter"] = None
            st.session_state["tr_result_filter"] = ["Pending"]

        active_category = st.session_state.get("tr_category_filter")
        pending_is_active = active_category is None and st.session_state.get("tr_result_filter") == ["Pending"]

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Prop Edges", f"{edge_pct:.0f}%" if edge_pct is not None else "—", f"{edge_hits}/{edge_n} resolved" if edge_n else "no results yet", delta_color="off")
            st.button(
                "Show only Prop Edges", use_container_width=True, key="tr_tile_edge",
                type="primary" if active_category == "edge" else "secondary",
                on_click=_toggle_category_filter, args=("edge",),
            )
        with m2:
            st.metric("Anytime TD", f"{any_pct:.0f}%" if any_pct is not None else "—", f"{any_hits}/{any_n} resolved" if any_n else "no results yet", delta_color="off")
            st.button(
                "Show only Anytime TD", use_container_width=True, key="tr_tile_anytime",
                type="primary" if active_category == "td_anytime" else "secondary",
                on_click=_toggle_category_filter, args=("td_anytime",),
            )
        with m3:
            st.metric("First TD", f"{first_pct:.0f}%" if first_pct is not None else "—", f"{first_hits}/{first_n} resolved" if first_n else "no results yet", delta_color="off")
            st.button(
                "Show only First TD", use_container_width=True, key="tr_tile_first",
                type="primary" if active_category == "td_first" else "secondary",
                on_click=_toggle_category_filter, args=("td_first",),
            )
        with m4:
            st.metric("Pending", pending_count)
            st.button(
                "Show only Pending", use_container_width=True, key="tr_tile_pending",
                type="primary" if pending_is_active else "secondary",
                on_click=_filter_to_pending,
            )

        # ---- Closing Line Value ----
        # Independent of hit rate above - this measures whether the
        # market itself moved toward agreeing with each pick, not
        # whether it actually hit. See compute_clv's docstring for the
        # full explanation and the units (points for Prop Edge,
        # percentage points for the two TD categories - never combined
        # into one blended number since they're not the same unit).
        clv_summary = compute_clv_summary(all_picks)
        if clv_summary:
            st.markdown("###### 📉 Closing Line Value")
            clv1, clv2, clv3 = st.columns(3)
            clv_cols = {"edge": (clv1, "Prop Edge", "pts"), "td_anytime": (clv2, "Anytime TD", "pp"), "td_first": (clv3, "First TD", "pp")}
            for cat, (col, label, unit) in clv_cols.items():
                with col:
                    seg = clv_summary.get(cat)
                    if seg:
                        st.metric(f"{label} avg CLV", f"{seg['avg_clv']:+.1f} {unit}", f"{seg['n']} tracked", delta_color="off")
                    else:
                        st.metric(f"{label} avg CLV", "—", "no closing lines captured yet", delta_color="off")
            theme.info_popover(
                "**Closing Line Value (CLV)** measures whether the sportsbook line moved toward agreeing with "
                "each pick between when it was made and kickoff - independent of whether the pick actually hit. "
                "It's widely considered sports betting's most reliable long-run skill signal, since a market's "
                "closing line reflects the sharpest available consensus, and a single game's outcome carries a "
                "lot of noise a line move doesn't. Positive means the market came around to the picked side "
                "after the pick was made; negative means it moved the other way. \"Closing\" here is an honest "
                "approximation - the last live line this app happened to see before kickoff on some page load, "
                "not a literal final-seconds price - so a pick with no page load between it being made and "
                "kickoff has no CLV captured at all.",
                label="ℹ️ About Closing Line Value",
            )

        st.divider()

        # ---- Trend + breakdown charts ----
        # Built from resolved_picks only (Push excluded from the hit-rate
        # line the same way compute_hit_rate excludes it everywhere else;
        # the breakdown bar shows all three outcomes since that one IS
        # about composition, not a rate).
        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            st.markdown("###### Hit rate by week")
            scored_picks = [p for p in resolved_picks if p["status"] in ("Hit", "Miss")]
            if not scored_picks:
                st.caption("Not enough resolved picks yet to chart a trend.")
            else:
                trend_src = pd.DataFrame([
                    {
                        "season": p["season"], "week": p["week"],
                        "period": f"{p['season']} Wk {p['week']}",
                        "category": category_labels.get(p["category"], p["category"]),
                        "hit": 1 if p["status"] == "Hit" else 0,
                    }
                    for p in scored_picks
                ])
                period_order = (
                    trend_src[["season", "week", "period"]].drop_duplicates()
                    .sort_values(["season", "week"])["period"].tolist()
                )
                trend_df = trend_src.groupby(["period", "category"], as_index=False).agg(hits=("hit", "sum"), n=("hit", "count"))
                trend_df["hit_rate"] = trend_df["hits"] / trend_df["n"] * 100
                trend_line = alt.Chart(trend_df).mark_line(point=alt.OverlayMarkDef(size=60), strokeWidth=2).encode(
                    x=alt.X("period:N", title=None, sort=period_order),
                    y=alt.Y("hit_rate:Q", title="Hit rate", scale=alt.Scale(domain=[0, 100])),
                    color=alt.Color(
                        "category:N", title=None, sort=category_order,
                        scale=alt.Scale(domain=category_order, range=[category_colors[c] for c in category_order]),
                        legend=alt.Legend(orient="top"),
                    ),
                    tooltip=[
                        alt.Tooltip("period:N", title="Week"), alt.Tooltip("category:N", title="Category"),
                        alt.Tooltip("hit_rate:Q", title="Hit rate", format=".0f"),
                        alt.Tooltip("n:Q", title="Resolved picks"),
                    ],
                ).properties(height=260)
                st.altair_chart(trend_line, use_container_width=True)

        with chart_col2:
            st.markdown("###### Results by category")
            breakdown_src = pd.DataFrame([
                {"category": category_labels.get(p["category"], p["category"]), "Result": p["status"]}
                for p in all_picks if p["status"] in ("Hit", "Miss", "Push")
            ])
            if breakdown_src.empty:
                st.caption("Not enough resolved picks yet to chart a breakdown.")
            else:
                breakdown_df = breakdown_src.groupby(["category", "Result"], as_index=False).size().rename(columns={"size": "count"})
                # Status colors (reserved - never reused for identity elsewhere
                # on this page), same green/red the rest of the app already
                # uses for Hit/Miss-shaped outcomes (delta-up/down, confidence
                # badges), plus a neutral gray for Push.
                breakdown_bar = alt.Chart(breakdown_df).mark_bar(cornerRadiusEnd=3).encode(
                    x=alt.X("category:N", title=None, sort=category_order),
                    y=alt.Y("count:Q", title="Resolved picks"),
                    color=alt.Color(
                        "Result:N", title=None, sort=["Hit", "Miss", "Push"],
                        scale=alt.Scale(domain=["Hit", "Miss", "Push"], range=[theme.GOOD, theme.BAD, theme.SUB]),
                        legend=alt.Legend(orient="top"),
                    ),
                    order=alt.Order("Result:N", sort="ascending"),
                    tooltip=[alt.Tooltip("category:N", title="Category"), alt.Tooltip("Result:N", title="Result"), alt.Tooltip("count:Q", title="Count")],
                ).properties(height=260)
                breakdown_text = breakdown_bar.mark_text(color=theme.INK, fontWeight=600, dy=2).encode(
                    text=alt.Text("count:Q"),
                    order=alt.Order("Result:N", sort="ascending"),
                )
                st.altair_chart(breakdown_bar + breakdown_text, use_container_width=True)

        st.divider()

        # ---- Most Recent Picks: headshot + team-logo cards, same large-card
        # component (render_hotpick_cards/player_avatar_html) used on Hot
        # Picks, Game Center, Lineup Optimizer and the First TD podium - so a
        # tracked pick looks like the same site everywhere, per the standing
        # "large card + headshot" consistency rule. Capped at 8 (2 rows of
        # 4) and sorted most-recent-first; the full unfiltered list is still
        # the History table below.
        st.markdown("###### 🕒 Most Recent Picks")
        recent_source = sorted(all_picks, key=lambda x: (x["season"], x["week"]), reverse=True)[:8]
        recent_rows = []
        for p in recent_source:
            meta_row = tr_meta.loc[p["player"]] if p["player"] in tr_meta.index else None
            detail = p.get("detail", {})
            actual = p.get("actual", {})
            if p["category"] == "edge":
                prediction = f"{detail.get('direction', '')} {detail.get('prop_line', '')} {detail.get('stat', '')}"
                actual_text = f"Actual: {actual['actual_value']:.1f}" if "actual_value" in actual else "Actual: —"
            elif p["category"] == "td_anytime":
                prediction = f"Anytime TD ({detail.get('predicted_pct', 0):.0f}% implied)"
                actual_text = f"Actual: {actual['actual_tds']} TD{'s' if actual.get('actual_tds') != 1 else ''}" if "actual_tds" in actual else "Actual: —"
            else:
                prediction = f"First TD ({detail.get('predicted_pct', 0):.0f}% implied)"
                actual_text = {"Hit": "Actual: Scored first", "Miss": "Actual: Did not score first"}.get(p["status"], "Actual: —")
            result_cls = {"Hit": "result-hit", "Miss": "result-miss", "Push": "result-push", "Pending": "result-pending"}[p["status"]]
            result_icon = {"Hit": "✅", "Miss": "❌", "Push": "➖", "Pending": "⏳"}[p["status"]]
            recent_rows.append({
                "player": p["player"], "team": p["team"], "position": p.get("position", ""),
                "headshot_url": meta_row.get("headshot_url") if meta_row is not None else None,
                "team_color": (meta_row.get("team_color") if meta_row is not None else None) or "#444444",
                "_prediction": prediction, "_actual": actual_text, "_result_cls": result_cls, "_result_icon": result_icon,
                "_result": p["status"], "_week_label": f"{p['season']} Wk {p['week']}",
            })

        def _recent_pick_body(row: dict) -> str:
            return (
                f'<div class="stat-label" style="margin-top:8px;">{row["_week_label"]}</div>'
                f'<div style="margin-top:2px; font-size:14px;">{row["_prediction"]}</div>'
                f'<div class="stat-label" style="margin-top:2px; text-transform:none;">{row["_actual"]}</div>'
                f'<div class="result-badge {row["_result_cls"]}">{row["_result_icon"]} {row["_result"]}</div>'
            )

        render_hotpick_cards(recent_rows, body_fn=_recent_pick_body, cols_per_row=4, headshot_px=96)

        st.divider()
        header_col, clear_col = st.columns([5, 1])
        with header_col:
            if active_category:
                st.markdown(f"##### History — filtered to **{category_labels[active_category]}**")
            else:
                st.markdown("##### History")
        with clear_col:
            if active_category:
                st.button("✕ Clear filter", key="tr_clear_category", on_click=_toggle_category_filter, args=(active_category,))
        # Opponent lookup for the History table's "Opp" logo column. Every
        # tracked pick stores its nflverse game_id, so the opponent comes
        # straight from the schedule - which means picks saved BEFORE this
        # column existed get an opponent too, with no change to stored data.
        # Fallback: nflverse game_ids are "{season}_{week}_{away}_{home}",
        # so the teams can be read from the id itself if the schedule
        # doesn't have that game for any reason.
        try:
            _hist_sched = get_schedule()
            _game_teams = {
                g["game_id"]: (g["away_team"], g["home_team"])
                for _, g in _hist_sched[["game_id", "away_team", "home_team"]].iterrows()
            }
        except Exception:
            _game_teams = {}

        def _pick_opponent(pick):
            gid = pick.get("game_id") or ""
            teams = _game_teams.get(gid)
            if teams is None:
                parts = gid.split("_")
                teams = (parts[2], parts[3]) if len(parts) == 4 else None
            if teams is None:
                return None
            away, home = teams
            return home if pick.get("team") == away else away if pick.get("team") == home else None

        def _logo(team):
            url = team_logos.get(team) if team else None
            return url if url is not None and pd.notna(url) else None

        hist_rows = []
        for p in sorted(all_picks, key=lambda x: (x["season"], x["week"], x["player"]), reverse=True):
            detail = p.get("detail", {})
            actual = p.get("actual", {})
            if p["category"] == "edge":
                prediction = f"{detail.get('direction', '')} {detail.get('prop_line', '')} {detail.get('stat', '')}"
                actual_text = f"{actual['actual_value']:.1f}" if "actual_value" in actual else "—"
            elif p["category"] == "td_anytime":
                prediction = f"Anytime TD ({detail.get('predicted_pct', 0):.0f}% implied)"
                actual_text = (
                    f"{actual['actual_tds']} TD{'s' if actual['actual_tds'] != 1 else ''}"
                    if "actual_tds" in actual else "—"
                )
            else:
                prediction = f"First TD ({detail.get('predicted_pct', 0):.0f}% implied)"
                if p["status"] == "Hit":
                    actual_text = "Scored first"
                elif p["status"] == "Miss":
                    actual_text = "Did not score first"
                else:
                    actual_text = "—"
            meta_row = tr_meta.loc[p["player"]] if p["player"] in tr_meta.index else None
            raw_headshot = meta_row.get("headshot_url") if meta_row is not None else None
            pick_clv = compute_clv(p)
            clv_unit = "pts" if p["category"] == "edge" else "pp"
            hist_rows.append({
                "Season": p["season"], "Week": p["week"], "Category": category_labels.get(p["category"], p["category"]),
                "Headshot": sized_headshot(raw_headshot, 40) if raw_headshot and pd.notna(raw_headshot) else None,
                "Player": p["player"],
                "Team": _logo(p["team"]),
                "Opp": _logo(_pick_opponent(p)),
                "Prediction": prediction,
                "Actual": actual_text, "Result": p["status"],
                "CLV": f"{pick_clv:+.1f} {clv_unit}" if pick_clv is not None else "—",
            })
        hist_df = pd.DataFrame(hist_rows)

        result_filter = st.multiselect(
            "Result", ["Hit", "Miss", "Push", "Pending"], default=["Hit", "Miss", "Push", "Pending"], key="tr_result_filter",
        )
        display_df = hist_df[hist_df["Result"].isin(result_filter)] if result_filter else hist_df
        if active_category:
            display_df = display_df[display_df["Category"] == category_labels[active_category]]
        # Column order is the standard layout (Sep 2026): pick context
        # first (season/week/category), then who (photo, name, team logo,
        # opponent logo), then the call and how it turned out.
        st.dataframe(
            display_df, width="content", hide_index=True, row_height=38,
            column_order=["Season", "Week", "Category", "Headshot", "Player", "Team", "Opp",
                          "Prediction", "Actual", "Result", "CLV"],
            column_config={
                "Headshot": st.column_config.ImageColumn(width="small"),
                "Team": st.column_config.ImageColumn("Team", width="small", help="Player's team"),
                "Opp": st.column_config.ImageColumn("Opp", width="small", help="Opponent in that game"),
            },
        )

    if pick_tracker_store.using_local_fallback(st.secrets):
        st.caption(
            "⚠️ Google Sheets isn't configured (or isn't reachable right now) - tracked picks are saved "
            "locally on this server instead and won't survive a redeploy. Same setup as My Rosters and the "
            "Bet Slip Tracker - see README."
        )

# Runs after the Fantasy Lineups / Prop Bets / Hot Picks / Track Record
# if-elif-else above completes, so this shows once at the true bottom of
# the page on every tab and every sub-tab, regardless of which side is
# active.
theme.render_footer()

