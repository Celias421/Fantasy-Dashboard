"""resolve_pending_picks with fake schedule/stats/storage - Safe Play
grading, and picks waiting (not marked Miss) when a final game's stats
haven't been published yet."""

import ast
import datetime
from pathlib import Path

import pandas as pd

_SRC = (Path(__file__).resolve().parent.parent / "dashboard.py").read_text()
_TREE = ast.parse(_SRC)
_nodes = [n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "resolve_pending_picks"]


class _Store:
    def __init__(self, picks):
        self.picks = picks
        self.saved = []

    def load_picks(self, _secrets):
        return [dict(p) for p in self.picks]

    def update_picks(self, _secrets, updated):
        self.saved.extend(updated)


class _St:
    secrets = {}


def _run(picks, stats_rows, final_games=("g1", "g2")):
    store = _Store(picks)
    stats = pd.DataFrame(stats_rows, columns=["season", "week", "team", "player_id", "rushing_yards",
                                              "rushing_tds", "receiving_tds", "fantasy_points_ppr"])

    def get_stats():
        return stats
    get_stats.clear = lambda: None

    sched = pd.DataFrame({"game_id": ["g1", "g2", "g3"],
                          "home_score": [20 if g in final_games else None for g in ("g1", "g2", "g3")]})
    ns = {"pick_tracker_store": store, "st": _St, "get_schedule": lambda: sched, "get_stats": get_stats,
          "get_first_td_scorers": lambda: pd.DataFrame(columns=["game_id", "first_td_player_id"]),
          "CURRENT_SEASON": 2026, "datetime": datetime, "pd": pd}
    exec(compile(ast.Module(body=_nodes, type_ignores=[]), "x", "exec"), ns)
    result = ns["resolve_pending_picks"]()
    return result, {p["id"]: p for p in store.saved}


def _pick(pid, cat, team, game, player_id, detail):
    return {"id": pid, "season": 2026, "week": 3, "category": cat, "team": team, "game_id": game,
            "player_id": player_id, "status": "Pending", "detail": detail}


def test_safe_play_grading_and_unpublished_stats_wait():
    picks = [
        _pick("s_hit", "safe", "BAL", "g1", "p1", {"floor_ppr": 8.0}),
        _pick("s_miss", "safe", "BAL", "g1", "p2", {"floor_ppr": 8.0}),
        _pick("s_dnp", "safe", "BAL", "g1", "p3", {"floor_ppr": 8.0}),        # team has stats, he doesn't
        _pick("e_wait", "edge", "KC", "g2", "p9",                              # game final, KC stats not out
              {"stat_col": "rushing_yards", "prop_line": 50.5, "direction": "▲ Over"}),
        _pick("e_future", "edge", "BAL", "g3", "p1",                           # game not final
              {"stat_col": "rushing_yards", "prop_line": 50.5, "direction": "▲ Over"}),
    ]
    stats = [
        (2026, 3, "BAL", "p1", 80, 0, 0, 14.5),
        (2026, 3, "BAL", "p2", 10, 0, 0, 4.0),
    ]
    (n_resolved, n_pending), saved = _run(picks, stats)
    assert saved["s_hit"]["status"] == "Hit" and saved["s_hit"]["actual"]["actual_ppr"] == 14.5
    assert saved["s_miss"]["status"] == "Miss"
    assert saved["s_dnp"]["status"] == "Push"
    assert "e_wait" not in saved and "e_future" not in saved   # both still Pending
    assert (n_resolved, n_pending) == (3, 2)


def test_prior_season_stats_do_not_grade_this_season():
    picks = [_pick("e1", "edge", "BAL", "g1", "p1",
                   {"stat_col": "rushing_yards", "prop_line": 50.5, "direction": "▲ Over"})]
    stats = [(2025, 3, "BAL", "p1", 99, 0, 0, 20.0)]   # same week number, wrong season
    (n_resolved, _), saved = _run(picks, stats)
    assert n_resolved == 0 and not saved
