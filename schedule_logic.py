"""
schedule_logic.py
Pure schedule/bye-week/lineup-optimization logic, pulled out of
dashboard.py specifically so it can be unit tested (see tests/) without
importing the whole Streamlit script - dashboard.py runs top-to-bottom
as a page load (network calls, st.secrets, st.cache_data, the works), so
`import dashboard` in a test file would try to load live data and a
Streamlit runtime just to reach one function. Nothing in this module
touches Streamlit, secrets, or the network - every function here is a
plain function of the arguments passed to it, which is exactly what
makes the class of bug this file exists to catch (the "Bye this week"
mislabel from teams_playing_this_week - see its docstring) testable with
a small hand-built DataFrame instead of needing a live season to
reproduce.

dashboard.py imports everything it needs from here rather than
redefining it - this is the one source of truth for this logic, the same
way theme.py is for styling and roster_store.py is for persistence.
"""

import pandas as pd

# ---- Lineup slot construction -------------------------------------------

FLEX_ELIGIBLE = {"RB", "WR", "TE"}
# Order fixed slots are filled/displayed in - FLEX is always last so it
# only sees leftovers after every dedicated position slot is filled first.
SLOT_ORDER = ["QB", "RB", "WR", "TE", "FLEX"]


def build_lineup_slots(lineup_settings: dict) -> list:
    """Turns {"QB": 1, "RB": 2, ...} into a flat, ordered slot list like
    ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"] - what
    optimize_lineup and the lineup display actually iterate over. A slot
    count of 0 just omits that slot entirely."""
    slots = []
    for slot in SLOT_ORDER:
        slots.extend([slot] * lineup_settings.get(slot, 0))
    return slots


def matchup_adjustment(rank) -> float:
    """Turn an opponent's fantasy-points-allowed rank (1=toughest,
    32=easiest) into a simple, transparent multiplier on season-average
    points: 0.85x facing the toughest defense, 1.15x facing the easiest,
    linear in between. Deliberately simple (not a real statistical model)
    so the "why" behind a projection is easy to explain, rather than a
    black box."""
    if rank is None:
        return 1.0
    return 0.85 + (rank - 1) / 31 * 0.30


def optimize_lineup(proj_df: pd.DataFrame, lineup_slots: list):
    """Greedy best-lineup pick: fill QB/RB/RB/WR/WR/TE (or whatever the
    roster's own lineup_slots says - counts vary by league) with the
    highest projected-points player at each position, then each FLEX slot
    with the best leftover RB/WR/TE. lineup_slots order matters - fixed
    slots always get first pick and FLEX only sees what's left, which is
    what makes any number of FLEX slots work correctly, not just one.
    Players marked "Out" or on a "Bye" are excluded entirely (not real
    decisions - they literally can't play), but Doubtful/Questionable are
    left in and just flagged - those are real game-time calls, not TPS's
    to make for you.
    Returns (lineup: dict slot_key -> row or None, bench: DataFrame,
    unfillable: list of slots with no eligible player left)."""
    available = proj_df[~proj_df["injury_status"].isin(["Out", "Bye"])].copy()
    used_players = set()
    lineup = {}
    unfillable = []

    for i, slot in enumerate(lineup_slots):
        eligible_positions = FLEX_ELIGIBLE if slot == "FLEX" else {slot}
        pool = available[
            available["position"].isin(eligible_positions) & (~available["player"].isin(used_players))
        ].sort_values("proj_points", ascending=False)
        key = f"{slot}_{i}"
        if pool.empty:
            lineup[key] = None
            unfillable.append(slot)
        else:
            pick = pool.iloc[0]
            lineup[key] = pick
            used_players.add(pick["player"])

    bench = proj_df[~proj_df["player"].isin(used_players)].sort_values("proj_points", ascending=False)
    return lineup, bench, unfillable


# ---- Schedule / bye-week logic ------------------------------------------

def teams_playing_this_week(schedule: pd.DataFrame) -> set:
    """Every team with a game in the soonest upcoming week - NOT the same
    as "has a next opponent" (build_next_opponent_map finds each team's
    next game regardless of how far out it is, so a team on a bye this
    week would still show a normal-looking matchup for the following
    week). This is specifically "will they play in the next slate of
    games", which is what actually matters for a lineup decision.

    Deliberately two separate steps over the schedule, not one filtered
    pass: unplayed games alone decide WHICH week is "this week" (the
    soonest week that still has a game left to play), but every team in
    THAT week's full slate counts as playing it - including a team whose
    Thursday (or international-window) game has already kicked off and
    posted a score before the rest of the week's Sunday/Monday games
    have. Filtering the team list itself to only-unplayed-games (as this
    used to) made an already-played-this-week team disappear from the
    set entirely, indistinguishable from an actual bye - which is
    exactly the bug that had a player marked "Bye this week" in the
    lineup tools right after his team's Thursday game finished, even
    though he'd already played (and been started) that week. See
    tests/test_schedule_logic.py::test_teams_playing_this_week_keeps_team_whose_game_already_finished
    for the regression test."""
    upcoming = schedule[schedule["home_score"].isna()]
    if upcoming.empty:
        return set()
    next_week = upcoming["week"].min()
    week_games = schedule[schedule["week"] == next_week]
    return set(week_games["home_team"]) | set(week_games["away_team"])


def format_gametime(gametime) -> str:
    """'HH:MM' (24h, Eastern) -> '1:00 PM'. Just the time, with no
    weekday/date attached - used where the date is already shown
    separately (the Matchups table's own Kickoff column, game-picker
    labels) so the time isn't duplicated with format_kickoff()'s
    "Sun 1:00 PM" form."""
    if not gametime or pd.isna(gametime):
        return ""
    try:
        return pd.Timestamp(f"2000-01-01 {gametime}").strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return ""


def format_kickoff(gameday, gametime) -> str:
    """'gameday' (a date) and 'gametime' (a "HH:MM", 24h Eastern string) -
    nflverse's schedule keeps them as two separate columns - combined into
    one short label like "Sun 1:00 PM". Returns "" if either piece is
    missing (a game far enough out that a time hasn't been set yet)."""
    if pd.isna(gameday) or not gametime or pd.isna(gametime):
        return ""
    try:
        ts = pd.Timestamp(f"{pd.Timestamp(gameday).strftime('%Y-%m-%d')} {gametime}")
    except (ValueError, TypeError):
        return ""
    return ts.strftime("%a %-I:%M %p")


def build_next_opponent_map(schedule: pd.DataFrame) -> pd.DataFrame:
    """team -> next scheduled opponent + week + home/away + kickoff time,
    based on games not yet played. Kickoff comes along for the ride so
    every matchup label built from this map (cards, badges, lineup rows,
    roster comparison) can show "Sun 1:00 PM" alongside the opponent
    instead of just the week number."""
    upcoming = schedule[schedule["home_score"].isna()].sort_values("gameday")
    rows = []
    for _, g in upcoming.iterrows():
        kickoff = format_kickoff(g.get("gameday"), g.get("gametime"))
        rows.append((g["home_team"], g["away_team"], g["week"], True, kickoff))
        rows.append((g["away_team"], g["home_team"], g["week"], False, kickoff))
    if not rows:
        return pd.DataFrame(columns=["team", "opponent", "week", "is_home", "kickoff"])
    next_opp = pd.DataFrame(rows, columns=["team", "opponent", "week", "is_home", "kickoff"])
    return next_opp.drop_duplicates(subset="team", keep="first")


def build_team_implied_totals(schedule: pd.DataFrame) -> dict:
    """team -> Vegas-implied point total for its next scheduled game,
    derived from that game's total_line (over/under) and spread_line.
    Confirmed against home_moneyline/away_moneyline on real data that
    nflverse encodes spread_line as the HOME team's spread where POSITIVE
    means the home team is favored (the opposite sign of how a spread is
    usually spoken aloud, e.g. "KC -3") - so the standard team-total split
    is:
        home_implied = (total_line + spread_line) / 2
        away_implied = (total_line - spread_line) / 2
    A team's own implied total is a single, well-established handicapping
    proxy for "how good an offensive environment is this expected to be
    for this team this week" - useful context (and a ranking signal)
    across every stat type, not just one. Same "games not yet played,
    first upcoming one wins" scoping as build_next_opponent_map, so a bye
    team simply has no entry (callers use .get() and treat that as
    unknown, never as a penalty). A team whose next game has no posted
    line yet (early in the week) is skipped the same way."""
    upcoming = schedule[schedule["home_score"].isna()].sort_values("gameday")
    totals: dict = {}
    for _, g in upcoming.iterrows():
        total_line = g.get("total_line")
        spread_line = g.get("spread_line")
        if pd.isna(total_line) or pd.isna(spread_line):
            continue
        totals.setdefault(g["home_team"], round((total_line + spread_line) / 2, 1))
        totals.setdefault(g["away_team"], round((total_line - spread_line) / 2, 1))
    return totals
