"""
Regression tests for dashboard.compute_summary - the function behind the
consistency label on every card and the Safe Plays ranking.

dashboard.py runs the whole Streamlit app at import time, so it can't be
imported in a test. Instead this pulls ONLY compute_summary and the two
constants it depends on out of the source with `ast` and executes them in
isolation - no Streamlit code runs.
"""

import ast
from pathlib import Path

import pandas as pd
import pytest

_SRC = (Path(__file__).resolve().parent.parent / "dashboard.py").read_text()
_TREE = ast.parse(_SRC)
_WANTED_ASSIGNS = {"MIN_GAMES_FOR_CONSISTENCY", "CONSISTENCY_HIGH_CV"}
_nodes = [
    n for n in _TREE.body
    if (isinstance(n, ast.FunctionDef) and n.name == "compute_summary")
    or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in _WANTED_ASSIGNS for t in n.targets))
]
_ns = {"pd": pd}
exec(compile(ast.Module(body=_nodes, type_ignores=[]), "dashboard_extract", "exec"), _ns)
compute_summary = _ns["compute_summary"]
MIN_GAMES = _ns["MIN_GAMES_FOR_CONSISTENCY"]
HIGH_CV = _ns["CONSISTENCY_HIGH_CV"]


def _games(values):
    return pd.DataFrame({"week": range(1, len(values) + 1), "fantasy_points_ppr": values})


def test_negative_average_is_never_high_consistency():
    # The Sep 2026 QA bug: a negative mean gave a negative cv, which passed
    # "cv < threshold" and ranked the player #1 in Safe Plays.
    avg, _, _, consistency, cv = compute_summary(_games([-0.5, 0.2, -0.1, 0.1]), "fantasy_points_ppr")
    assert avg < 0
    assert consistency == "N/A"
    assert cv is None


def test_zero_average_is_na():
    _, _, _, consistency, cv = compute_summary(_games([0.0, 0.0, 0.0, 0.0]), "fantasy_points_ppr")
    assert consistency == "N/A"
    assert cv is None


def test_steady_positive_scorer_is_high():
    _, _, _, consistency, cv = compute_summary(_games([20.0, 21.0, 19.5, 20.5]), "fantasy_points_ppr")
    assert consistency == "High"
    assert 0 <= cv < HIGH_CV


def test_volatile_scorer_is_low():
    _, _, _, consistency, _ = compute_summary(_games([2.0, 30.0, 1.0, 28.0]), "fantasy_points_ppr")
    assert consistency == "Low"


def test_below_min_games_is_na_even_if_steady():
    values = [20.0] * (MIN_GAMES - 1)
    _, _, _, consistency, _ = compute_summary(_games(values), "fantasy_points_ppr")
    assert consistency == "N/A"


def test_missing_stat_column_returns_safe_defaults():
    result = compute_summary(pd.DataFrame({"week": [1, 2, 3]}), "fantasy_points_ppr")
    assert result == (0.0, 0.0, 0.0, "N/A", None)
