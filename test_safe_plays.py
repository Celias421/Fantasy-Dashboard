"""Safe Plays rule (best_safe_line): best-paying line - alt or main - a
player cleared in 90%+ of recent games, never worse than -400."""

import ast
from pathlib import Path

import pandas as pd
import pytest

from odds_math import break_even_prob, american_to_decimal

_SRC = (Path(__file__).resolve().parent.parent / "dashboard.py").read_text()
_TREE = ast.parse(_SRC)
_nodes = [
    n for n in _TREE.body
    if (isinstance(n, ast.FunctionDef) and n.name in ("best_safe_line", "_num"))
    or (isinstance(n, ast.Assign) and any(getattr(t, "id", "").startswith("SAFE_") for t in n.targets))
]
g = {"pd": pd, "break_even_prob": break_even_prob, "american_to_decimal": american_to_decimal}
exec(compile(ast.Module(body=_nodes, type_ignores=[]), "x", "exec"), g)
best_safe_line = g["best_safe_line"]

# receiving yards, last 10 games
VALS = [52, 61, 38, 70, 44, 58, 66, 49, 35, 72]


def _ln(point, side, price, kind="Alt"):
    return {"point": point, "side": side, "price": price, "best_price": None if price is None else price + 10,
            "best_book": "fanduel", "kind": kind}


def test_picks_best_paying_line_that_clears_90_percent():
    lines = [_ln(24.5, "Over", -600), _ln(29.5, "Over", -400), _ln(34.5, "Over", -300),
             _ln(39.5, "Over", -220), _ln(49.5, "Over", -110, "Main")]
    pick = best_safe_line(VALS, lines)
    # 34.5 cleared 10/10, 39.5 cleared 8/10 (fails 90%), -600 is too expensive
    assert pick["point"] == 34.5 and pick["price"] == -300 and pick["hits"] == 10 and pick["n"] == 10
    assert pick["break_even"] == pytest.approx(0.75)
    assert pick["room"] == pytest.approx(0.25)
    assert pick["direction"] == "▲ Over" and pick["kind"] == "Alt"


def test_nine_of_ten_qualifies_but_eight_does_not():
    assert best_safe_line(VALS, [_ln(37.5, "Over", -200)])["hits"] == 9       # misses only the 35
    assert best_safe_line(VALS, [_ln(39.5, "Over", -200)]) is None           # 8 of 10


def test_unders_work_and_landing_on_the_line_is_not_a_hit():
    assert best_safe_line(VALS, [_ln(80.5, "Under", -350)])["direction"] == "▼ Under"
    # 72 lands exactly on 72 -> not cleared, so Over 72 can't be 10/10... and Under 72 is 9/10
    pick = best_safe_line(VALS, [_ln(72, "Under", -250)])
    assert pick["hits"] == 9


def test_price_limit_and_minimum_games():
    assert best_safe_line(VALS, [_ln(20.5, "Over", -450)]) is None           # worse than -400
    assert best_safe_line(VALS[:4], [_ln(20.5, "Over", -200)]) is None       # only 4 games
    assert best_safe_line(VALS, [_ln(20.5, "Over", None)]) is None           # no price
