"""
dashboard.py
Run locally with: streamlit run dashboard.py
Also the entry point when hosted on Streamlit Community Cloud.
"""

import datetime

import altair as alt
import pandas as pd
import streamlit as st

from config import CURRENT_SEASON, PROP_MARKET_MAP, TEAM_CITY, INDOOR_ROOF_STATES
from data_loader import (
    load_starter_stats, load_player_meta, load_team_meta, load_defense_ranks, load_schedule,
    load_current_injuries, load_prop_lines, geocode_city, load_game_weather,
    load_all_seasons_schedule,
)

st.set_page_config(page_title="The Prop Shop", layout="wide", page_icon="🏈")

st.markdown("""
<style>
.player-card {
    background: #1a1c24;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 14px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
}
.player-card img { border-radius: 50%; object-fit: cover; }
.stat-big { font-size: 26px; font-weight: 700; margin-top: 6px; }
.stat-label { font-size: 12px; color: #999; }
.delta-up { color: #4CAF50; font-weight: 600; }
.delta-down { color: #F44336; font-weight: 600; }
.delta-flat { color: #999; font-weight: 600; }
.consistency-badge {
    display: inline-block; font-size: 11px; padding: 2px 8px;
    border-radius: 10px; margin-top: 6px; margin-right: 4px; background: #2a2d3a; color: #ccc;
}
.matchup-badge {
    display: inline-block; font-size: 11px; padding: 2px 8px;
    border-radius: 10px; margin-top: 6px; margin-right: 4px; background: #2a2d3a;
    /* text color is set inline per-badge, gradient by matchup difficulty */
}
.injury-badge {
    display: inline-block; font-size: 11px; padding: 2px 8px;
    border-radius: 10px; margin-top: 6px; margin-right: 4px; background: #3a2323; color: #e08a8a;
}
</style>
""", unsafe_allow_html=True)

PROP_STATS_BY_POSITION = {
    "QB": ["passing_yards", "passing_tds", "rushing_yards", "fantasy_points_ppr"],
    "RB": ["rushing_yards", "rushing_tds", "receiving_yards", "fantasy_points_ppr"],
    "WR": ["receiving_yards", "receptions", "receiving_tds", "fantasy_points_ppr"],
    "TE": ["receiving_yards", "receptions", "receiving_tds", "fantasy_points_ppr"],
}

ODDS_API_KEY = st.secrets.get("ODDS_API_KEY", "")


@st.cache_data(ttl=3600)
def get_stats() -> pd.DataFrame:
    return load_starter_stats()


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
    24-hour window survives restarts, not just page reloads."""
    props_df, td_df = load_prop_lines(ODDS_API_KEY)
    return props_df, td_df, datetime.datetime.now()


def get_prop_lines() -> pd.DataFrame:
    df, _, _ = _get_prop_lines_with_timestamp()
    return df


def get_anytime_td_odds() -> pd.DataFrame:
    """player -> implied_prob (0-100): the market's implied chance a
    player scores any touchdown this week. See load_prop_lines for why
    this is kept separate from get_prop_lines()."""
    _, td_df, _ = _get_prop_lines_with_timestamp()
    return td_df


def get_prop_lines_updated_at() -> datetime.datetime:
    _, _, updated_at = _get_prop_lines_with_timestamp()
    return updated_at


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


def compute_summary(player_df: pd.DataFrame, stat: str):
    """Return (avg, last_game_value, trend_vs_prior_avg, consistency_label).
    Expects a single season's worth of rows already."""
    weeks = player_df.sort_values("week")
    if stat not in weeks.columns or weeks[stat].dropna().empty:
        return 0.0, 0.0, 0.0, "N/A"

    values = weeks[stat].fillna(0)
    season_avg = values.mean()
    last_val = values.iloc[-1]
    trend = last_val - values.iloc[:-1].mean() if len(values) > 1 else 0.0

    std = values.std() if len(values) > 1 else 0.0
    cv = (std / season_avg) if season_avg else 0.0
    if season_avg == 0:
        consistency = "N/A"
    elif cv < 0.25:
        consistency = "High"
    elif cv < 0.5:
        consistency = "Medium"
    else:
        consistency = "Low"

    return season_avg, last_val, trend, consistency


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


def build_next_opponent_map(schedule: pd.DataFrame) -> pd.DataFrame:
    """team -> next scheduled opponent + week + home/away, based on games
    not yet played."""
    upcoming = schedule[schedule["home_score"].isna()].sort_values("gameday")
    rows = []
    for _, g in upcoming.iterrows():
        rows.append((g["home_team"], g["away_team"], g["week"], True))
        rows.append((g["away_team"], g["home_team"], g["week"], False))
    if not rows:
        return pd.DataFrame(columns=["team", "opponent", "week", "is_home"])
    next_opp = pd.DataFrame(rows, columns=["team", "opponent", "week", "is_home"])
    return next_opp.drop_duplicates(subset="team", keep="first")


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
    """Soft red (rank 1, toughest defense) -> soft yellow -> soft green
    (rank max_rank, easiest defense) text color for a matchup badge.
    Colors are pastel/muted rather than pure red/green so they stay
    readable as text on the badges' dark background."""
    t = (rank - 1) / max(max_rank - 1, 1)
    t = min(max(t, 0.0), 1.0)
    stops = [(0.0, (255, 107, 107)), (0.5, (255, 209, 102)), (1.0, (143, 214, 168))]
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


def _matchup_label(team: str, position: str, stat: str, next_opp_map: pd.DataFrame, defense_ranks: pd.DataFrame):
    """(text, rank) for a team's next scheduled opponent - text like
    '🏠 vs OPP — #N toughest' (home) or '✈️ @ OPP — #N toughest' (away),
    rank is that opponent's defensive rank (1=toughest, 32=easiest)
    against `stat` for `position` this season, for color-coding the
    badge. None if there's no upcoming game or no rank data for that
    stat (e.g. a bye week, or a stat with no _rank column)."""
    rank_col = f"{stat}_rank"
    opp_row = next_opp_map[next_opp_map["team"] == team]
    if opp_row.empty or rank_col not in defense_ranks.columns:
        return None
    opponent = opp_row["opponent"].iloc[0]
    is_home = bool(opp_row["is_home"].iloc[0])
    dr = defense_ranks[(defense_ranks["position"] == position) & (defense_ranks["team"] == opponent)]
    if dr.empty:
        return None
    icon = _icon_html(HOME_ICON if is_home else AWAY_ICON)
    prefix = "vs" if is_home else "@"
    rank = int(dr[rank_col].iloc[0])
    return f"{icon} {prefix} {opponent} — #{rank} toughest", rank


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
    defense_ranks = get_defense_ranks()
    next_opp_map = build_next_opponent_map(get_schedule())
    prop_market = PROP_MARKET_MAP.get(sort_stat)

    summary_rows = []
    for player, pdf in view.groupby("player"):
        avg, last, trend, consistency = compute_summary(pdf, sort_stat)
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
            "matchup_label": matchup_label,
            "matchup_rank": matchup_rank,
            "injury_label": injury_label,
        })
    if not summary_rows:
        return pd.DataFrame()
    return pd.DataFrame(summary_rows).sort_values("avg", ascending=False)


def render_player_cards(summary_df: pd.DataFrame, sort_stat: str, cols_per_row: int = 4):
    """Render the standard player-card grid. Used by both the Overview and
    Game Center tabs - a style change here applies everywhere at once."""
    for start in range(0, len(summary_df), cols_per_row):
        chunk = summary_df.iloc[start:start + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, (_, p) in zip(cols, chunk.iterrows()):
            with col:
                photo = sized_headshot(p["headshot_url"], 96) if pd.notna(p["headshot_url"]) else ""
                img_tag = f'<img src="{photo}" width="96" height="96" onerror="this.style.display=\'none\'"/>' if photo else ""

                badges = f'<div class="consistency-badge">Consistency: {p["consistency"]}</div>'
                if pd.notna(p.get("matchup_label")):
                    badges += matchup_badge_html(p["matchup_label"], int(p["matchup_rank"]))
                if pd.notna(p.get("td_odds_pct")):
                    badges += anytime_td_badge_html(p["td_odds_pct"])
                if pd.notna(p.get("injury_label")):
                    badges += f'<div class="injury-badge">{p["injury_label"]}</div>'

                prop_line_text = f' (line {p["prop_line"]:.1f})' if p.get("has_prop") else ""

                st.markdown(f"""
                <div class="player-card" style="border-left: 4px solid {p['team_color']};">
                    <div style="display:flex; align-items:center; gap:12px;">
                        {img_tag}
                        <div>
                            <div style="font-weight:600;">{p['player']}</div>
                            <div style="font-size:12px; color:#999;">{team_logo_html(p['team'])}{p['position']} · {p['team']}</div>
                        </div>
                    </div>
                    <div class="stat-big">{p['avg']:.1f}</div>
                    <div class="stat-label">avg {sort_stat.replace('_', ' ')}{prop_line_text}</div>
                    <div style="margin-top:4px;">{delta_html(p['delta'], p.get('has_prop', False))}</div>
                    {badges}
                </div>
                """, unsafe_allow_html=True)


st.title("🏈 The Prop Shop")
st.caption(f"Current season: {CURRENT_SEASON}. Trend charts include prior seasons' data for longer-term context.")

if not ODDS_API_KEY:
    st.info("No prop odds API key configured yet - card deltas will show \"No prop line\" until one is added. See README for setup.", icon="ℹ️")
else:
    prop_updated_at = get_prop_lines_updated_at()
    refresh_col1, refresh_col2 = st.columns([3, 1])
    with refresh_col1:
        st.caption(f"Prop lines last pulled: {prop_updated_at.strftime('%a %-I:%M %p')} (auto-refreshes once a day to conserve API quota)")
    with refresh_col2:
        if st.button("🔄 Refresh prop lines now", use_container_width=True):
            _get_prop_lines_with_timestamp.clear()
            st.rerun()

if st.button("Refresh all data now"):
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


def team_logo_html(team, px: int = 20) -> str:
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

tab_overview, tab_deep_dive, tab_props, tab_game, tab_injuries, tab_matchups = st.tabs(
    ["📋 Overview", "🔍 Player Deep Dive", "🎯 Prop Comparator", "🏟️ Game Center", "🩹 Injuries", "🗓️ Matchups"]
)

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
    tab's button code ever runs."""
    st.session_state["game_week"] = week
    st.session_state["game_pick"] = game_label
    st.session_state["show_jump_toast"] = True

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
        st.caption(f"{len(summary_df)} players — {CURRENT_SEASON} season, ranked by {sort_stat.replace('_', ' ')}")
        st.caption(
            "Badge key: matchup badges show the upcoming opponent's defensive rank (color: red = toughest, "
            "green = easiest). 🎯 Anytime TD is the betting market's implied chance this player scores any "
            "touchdown this week — a market probability, not a Prop Shop projection. Hover a badge for details."
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
                st.image(sized_headshot(info["headshot_url"], 140), width=140)
        with c2:
            st.markdown(f"#### {info['player']}")
            st.markdown(f"**{team_logo_html(info['team'], px=24)}{info['position']} · {info['team']}**", unsafe_allow_html=True)
            matchup_result = get_matchup_label(info["team"], info["position"], "fantasy_points_ppr")
            if matchup_result:
                matchup_text, matchup_rank = matchup_result
                st.markdown(matchup_badge_html(matchup_text, matchup_rank, tag="span"), unsafe_allow_html=True)
            td_odds = get_anytime_td_odds()
            td_match = td_odds[td_odds["player"] == info["player"]] if not td_odds.empty else td_odds
            if not td_match.empty:
                st.markdown(anytime_td_badge_html(float(td_match["implied_prob"].iloc[0]), tag="span"), unsafe_allow_html=True)

        metrics_source = pdf_current if not pdf_current.empty else pdf_full
        avg, last, trend, consistency = compute_summary(metrics_source, "fantasy_points_ppr")
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
            line = alt.Chart(pdf_full).mark_line(point=True, color="#5B8DEF").encode(
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
                    color="#999999", strokeDash=[5, 4], size=2
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
                        color="#F5A623", strokeDash=[2, 2], size=2
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
                st.dataframe(rolling.set_index("week"), use_container_width=True)

            with st.expander("Full weekly stats (all seasons)"):
                st.dataframe(pdf_full.drop(columns=["period"]), use_container_width=True)

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
                player_colors = ["#5B8DEF", "#F5A623"]
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
                st.dataframe(pdf_a_full.drop(columns=["period"]), use_container_width=True)
            with st.expander(f"Full weekly stats — {player_b}"):
                st.dataframe(pdf_b_full.drop(columns=["period"]), use_container_width=True)

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
        st.markdown(f"{team_logo_html(prop_team, px=22)}**{position} · {prop_team}**", unsafe_allow_html=True)
        prop_td_odds = get_anytime_td_odds()
        prop_td_match = prop_td_odds[prop_td_odds["player"] == prop_player] if not prop_td_odds.empty else prop_td_odds
        if not prop_td_match.empty:
            st.markdown(anytime_td_badge_html(float(prop_td_match["implied_prob"].iloc[0]), tag="span"), unsafe_allow_html=True)

    with c2:
        prop_stat = st.selectbox("Stat", available_stats, key="prop_stat") if available_stats else None
    with c3:
        # Pre-fill with the live market line when we have one, else season average
        live_lines = get_prop_lines()
        market = PROP_MARKET_MAP.get(prop_stat) if prop_stat else None
        live_match = live_lines[(live_lines["player"] == prop_player) & (live_lines["market"] == market)] if market is not None and not live_lines.empty else pd.DataFrame()
        if not live_match.empty:
            default_line = round(float(live_match["point"].iloc[0]), 1)
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
                    scale=alt.Scale(domain=["Over", "Under", "Push"], range=["#4CAF50", "#F44336", "#999999"]),
                    legend=alt.Legend(title=None),
                ),
                tooltip=["week_label", prop_stat, "opponent_team", "result"],
            )
        )
        rule = alt.Chart(pd.DataFrame({"y": [prop_line]})).mark_rule(
            color="#e0e0e0", strokeDash=[6, 4], size=2
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
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.caption(
            f"Matchup rank is out of 32, based on {CURRENT_SEASON} season totals allowed to that position "
            "(#1 = toughest defense, #32 = easiest)."
        )
    else:
        st.info("No stats available for this player yet this season.")

# ---------------- Game Center (single-game breakdown) ----------------
with tab_game:
    st.subheader("Single Game Breakdown")
    st.caption("Pick one game and see every tracked starter from both teams - handy on light slates like Thursday or Monday night.")

    schedule = get_schedule()
    schedule = schedule.copy()
    schedule["gameday_fmt"] = pd.to_datetime(schedule["gameday"]).dt.strftime("%a %-m/%-d")
    schedule["game_label"] = schedule["gameday_fmt"] + " — " + schedule["away_team"] + " @ " + schedule["home_team"]

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
        st.caption(f"Player cards ranked by {sort_stat.replace('_', ' ')} (change this in the sidebar's \"Sort / rank by\").")

        gm1, gm2, gm3, gm4 = st.columns(4)
        gm1.metric("Matchup", f"{away} @ {home}")
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
                    f"### {team_logo_html(team, px=32)}{team} "
                    f"<span style='font-size:13px; color:#999; font-weight:400;'>({home_away_label})</span>",
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
                    render_player_cards(team_summary, sort_stat, cols_per_row=2)

# ---------------- Injuries (full league injury report) ----------------
with tab_injuries:
    st.subheader("Injury Report")

    all_injuries = get_injuries()
    if all_injuries.empty:
        st.info("No injury report available yet this week.")
    else:
        st.caption(
            f"Every player on the official NFL injury report for week {int(all_injuries['week'].iloc[0])} "
            f"({CURRENT_SEASON} season) - not just tracked starters, so you can catch handcuffs and "
            "breakout candidates too. Sourced from nflverse's copy of the official team-submitted reports."
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
                shown, use_container_width=True, hide_index=True,
                column_config={"Team Logo": st.column_config.ImageColumn(" ", width="small")},
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
    schedule_all["game_label"] = schedule_all["gameday_fmt"] + " — " + schedule_all["away_team"] + " @ " + schedule_all["home_team"]
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
                "Kickoff": g.get("gameday_fmt", ""),
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
                        scale=alt.Scale(range=["#ff6b6b", "#ffd166", "#8fd6a8"]),
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
            "Away Logo": st.column_config.ImageColumn(" ", width="large"),
            "Home Logo": st.column_config.ImageColumn(" ", width="large"),
            "Weather Risk": st.column_config.ProgressColumn(
                "Weather Risk", min_value=0, max_value=100, format="%.0f%%",
                help="Rough wind/rain severity score - higher means more likely to affect passing and kicking.",
            ),
        }

        if group_by_day:
            for day in matchups_df.sort_values("gameday")["day_name"].unique():
                day_df = matchups_df[matchups_df["day_name"] == day]
                st.markdown(f"**{day}**")
                st.dataframe(day_df[display_cols], use_container_width=True, hide_index=True, column_config=column_config)
        else:
            st.dataframe(matchups_df[display_cols], use_container_width=True, hide_index=True, column_config=column_config)

        st.caption(
            "\"Away @ Home\" convention throughout - the Home column is the team hosting. Implied totals split "
            "the game total by the spread. Weather is a live forecast (Open-Meteo, refreshes every few hours) for "
            "games within about 16 days, or the actual recorded conditions for games already played. Domed/closed-roof "
            "and neutral-site games show no weather since it doesn't apply or the venue differs from the "
            "home team's usual city."
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
