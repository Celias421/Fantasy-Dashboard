"""snapshot_hotpicks_for_tracking: per-category weekly saving (a new
category starts mid-week without re-saving the others), Safe Play
detail, and no picks for games already under way."""

import ast
import datetime
import uuid
from pathlib import Path

import pandas as pd

_SRC = (Path(__file__).resolve().parent.parent / "dashboard.py").read_text()
_TREE = ast.parse(_SRC)
_nodes = [
    n for n in _TREE.body
    if (isinstance(n, ast.FunctionDef) and n.name in ("snapshot_hotpicks_for_tracking", "_game_kickoff_timestamps"))
    or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "SAFE_PLAY_FLOOR_PCT" for t in n.targets))
]


class _Store:
    def __init__(self, tracked):
        self.tracked = tracked
        self.added = []

    def tracked_categories(self, _s, season, week):
        return set(self.tracked)

    def add_picks(self, _s, picks):
        self.added.extend(picks)


class _St:
    secrets = {}


def _run(tracked, now):
    store = _Store(tracked)
    sched = pd.DataFrame({
        "game_id": ["2026_05_KC_BAL", "2026_05_DAL_PHI"], "week": [5, 5], "home_score": [None, None],
        "home_team": ["BAL", "PHI"], "away_team": ["KC", "DAL"],
        "gameday": ["2026-10-04", "2026-10-05"], "gametime": ["13:00", "20:15"],
    })
    ns = {"pick_tracker_store": store, "st": _St, "CURRENT_SEASON": 2026, "uuid": uuid, "pd": pd,
          "datetime": datetime, "format_kickoff": lambda d, t: f"{d} {t}", "_now_eastern": lambda: pd.Timestamp(now)}
    exec(compile(ast.Module(body=_nodes, type_ignores=[]), "x", "exec"), ns)
    edge = [{"player": "Lamar Jackson", "team": "BAL", "position": "QB", "stat": "Pass Yds", "stat_col": "passing_yards",
             "prop_line": 220.5, "season_avg": 250.0, "direction": "▲ Over", "typical_price": -110}]
    safe = [{"player": "Derrick Henry", "team": "BAL", "position": "RB", "season_avg_ppr": 18.4, "cv": 0.14},
            {"player": "CeeDee Lamb", "team": "DAL", "position": "WR", "season_avg_ppr": 17.0, "cv": 0.18}]
    ns["snapshot_hotpicks_for_tracking"](edge, [], sched, safe)
    return store.added


def test_safe_plays_added_mid_week_without_resaving_edges():
    added = _run(tracked={"edge", "td_anytime", "td_first"}, now="2026-10-01 12:00")
    assert {p["category"] for p in added} == {"safe"}
    henry = next(p for p in added if p["player"] == "Derrick Henry")
    assert henry["detail"]["floor_ppr"] == 9.2 and henry["week"] == 5 and henry["game_id"] == "2026_05_KC_BAL"


def test_games_already_under_way_are_skipped():
    added = _run(tracked=set(), now="2026-10-04 14:00")    # BAL game kicked off at 1:00
    assert [p["player"] for p in added] == ["CeeDee Lamb"]


def test_nothing_when_every_category_already_saved():
    assert _run(tracked={"edge", "td_anytime", "td_first", "safe"}, now="2026-10-01 12:00") == []
