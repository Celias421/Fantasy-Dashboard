"""Top-TD-picks rule: league-wide top N per market with a per-game cap,
applied the same way to new suggestions and to old saved picks."""

import ast
from pathlib import Path

import pandas as pd

_SRC = (Path(__file__).resolve().parent.parent / "dashboard.py").read_text()
_TREE = ast.parse(_SRC)
_nodes = [
    n for n in _TREE.body
    if (isinstance(n, ast.FunctionDef) and n.name in ("_top_ids", "select_top_td_rows", "countable_picks"))
    or (isinstance(n, ast.Assign) and any(getattr(t, "id", "").startswith("TD_") for t in n.targets))
]
g = {"pd": pd}
exec(compile(ast.Module(body=_nodes, type_ignores=[]), "x", "exec"), g)


def _row(player, team, anytime, first):
    return {"player": player, "team": team, "anytime_td_pct": anytime, "first_td_pct": first}


def test_one_game_cannot_flood_the_list():
    # 18 players from one game, all with high odds, plus 12 from other games
    rows = [_row(f"PHI{i}", "PHI" if i % 2 else "CHI", 60 - i, 25 - i) for i in range(18)]
    rows += [_row(f"X{i}", f"T{i}", 40 - i, 15 - i) for i in range(12)]
    game = lambda r: frozenset({"PHI", "CHI"}) if r["team"] in ("PHI", "CHI") else r["team"]
    top = g["select_top_td_rows"](rows, game)
    anytime = [r for r in top if r["track_anytime"]]
    first = [r for r in top if r["track_first"]]
    assert len(anytime) == g["TD_ANYTIME_TOP_N"] and len(first) == g["TD_FIRST_TOP_N"]
    assert sum(r["team"] in ("PHI", "CHI") for r in anytime) == g["TD_ANYTIME_PER_GAME"]
    assert sum(r["team"] in ("PHI", "CHI") for r in first) == g["TD_FIRST_PER_GAME"]
    # the two PHI/CHI anytime picks are that game's two highest
    assert {r["player"] for r in anytime if r["team"] in ("PHI", "CHI")} == {"PHI0", "PHI1"}


def test_row_without_odds_in_a_market_is_never_picked_there():
    top = g["select_top_td_rows"]([_row("A", "BAL", None, 20.0), _row("B", "KC", 50.0, float("nan"))], lambda r: r["team"])
    by = {r["player"]: r for r in top}
    assert by["A"]["track_first"] and not by["A"]["track_anytime"]
    assert by["B"]["track_anytime"] and not by["B"]["track_first"]


def test_old_saved_picks_are_filtered_by_the_same_rule():
    picks = [{"id": f"a{i}", "season": 2026, "week": 3, "category": "td_anytime", "game_id": "2026_03_PHI_CHI",
              "detail": {"predicted_pct": 50 - i}} for i in range(9)]
    picks += [{"id": f"f{i}", "season": 2026, "week": 3, "category": "td_first", "game_id": "2026_03_PHI_CHI",
               "detail": {"predicted_pct": 20 - i}} for i in range(9)]
    picks += [{"id": "e1", "season": 2026, "week": 3, "category": "edge", "game_id": "2026_03_PHI_CHI", "detail": {}}]
    kept = {p["id"] for p in g["countable_picks"](picks)}
    assert kept == {"a0", "a1", "f0", "e1"}      # 2 anytime + 1 first from that game; edges untouched
