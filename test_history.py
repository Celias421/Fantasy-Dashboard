"""Track Record historical views (season filter, season-so-far lines,
weekly table, position scorecard, season summary) - pulled out of
dashboard.py with ast so no Streamlit code runs."""

import ast
from pathlib import Path

import pandas as pd
import pytest

from odds_math import profit_on_stake

_SRC = (Path(__file__).resolve().parent.parent / "dashboard.py").read_text()
_TREE = ast.parse(_SRC)
_FUNCS = {
    "pick_profit", "filter_picks_by_season", "_record_text", "compute_running_by_week", "_group_stats",
    "compute_position_scorecard", "compute_season_summary", "compute_weekly_table", "confidence_tier",
}
_CONSTS = {
    "PROFIT_STAKE", "CATEGORY_LABELS", "BET_CATEGORIES", "ALL_CATEGORIES", "POSITION_ORDER",
    "CONFIDENCE_MIN_N", "CONFIDENCE_HIGH_PCT", "CONFIDENCE_LOW_PCT",
}
_nodes = [
    n for n in _TREE.body
    if (isinstance(n, ast.FunctionDef) and n.name in _FUNCS)
    or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in _CONSTS for t in n.targets))
]
_ns = {"profit_on_stake": profit_on_stake, "pd": pd}
exec(compile(ast.Module(body=_nodes, type_ignores=[]), "dashboard_extract", "exec"), _ns)
g = _ns


def _p(season, week, cat, status, pos="WR", price=-110):
    d = {"price": price} if cat != "safe" else {"floor_ppr": 5.0}
    return {"season": season, "week": week, "category": cat, "status": status, "position": pos, "detail": d}


PICKS = [
    _p(2025, 17, "edge", "Hit"),
    _p(2026, 1, "edge", "Hit"), _p(2026, 1, "edge", "Miss"),
    _p(2026, 2, "edge", "Hit"), _p(2026, 2, "edge", "Hit", pos="RB"),
    _p(2026, 2, "edge", "Push"),
    _p(2026, 2, "td_anytime", "Miss", pos="RB", price=150),
    _p(2026, 2, "safe", "Hit", pos="QB"), _p(2026, 2, "safe", "Miss", pos="QB"),
    _p(2026, 3, "edge", "Pending"),
]


def test_season_filter():
    assert len(g["filter_picks_by_season"](PICKS, 2026)) == 9
    assert len(g["filter_picks_by_season"](PICKS, None)) == 10


def test_running_line_accumulates_and_restarts_each_season():
    wk = g["compute_running_by_week"](PICKS)
    edge = wk[wk["category"] == "Prop Edge"].set_index("period")
    assert edge.loc["2025 Wk 17", "cum_rate"] == 100
    # 2026 restarts: wk1 1-1 (50%), wk2 adds 2-0 -> 3-1 (75%); the push isn't scored
    assert edge.loc["2026 Wk 1", "cum_rate"] == 50
    assert edge.loc["2026 Wk 2", "cum_hits"] == 3 and edge.loc["2026 Wk 2", "cum_n"] == 4
    assert edge.loc["2026 Wk 2", "cum_rate"] == 75
    # profit: wk1 +9.09 - 10, wk2 +9.09*2 + 0 push
    assert edge.loc["2026 Wk 2", "cum_profit"] == pytest.approx(9.09 * 3 - 10, abs=0.02)
    # pending never appears
    assert "2026 Wk 3" not in edge.index


def test_unpriced_picks_count_for_hit_rate_but_not_profit():
    wk = g["compute_running_by_week"](PICKS)
    safe = wk[wk["category"] == "Safe Play"].iloc[0]
    assert safe["cum_rate"] == 50 and safe["cum_profit"] == 0 and safe["cum_priced"] == 0
    summ = g["compute_season_summary"](PICKS)
    srow = summ[(summ["season"] == 2026) & (summ["category"] == "Safe Play")].iloc[0]
    assert srow["record"] == "1-1" and pd.isna(srow["profit"])


def test_weekly_table():
    t = g["compute_weekly_table"](PICKS)
    assert list(t[["Season", "Week"]].iloc[0]) == [2026, 2]      # newest first
    wk2 = t.iloc[0]
    assert wk2["Prop Edge"] == "2-0 (100%)"
    assert wk2["Anytime TD"] == "0-1 (0%)"
    assert wk2["Safe Play"] == "1-1 (50%)"
    assert wk2["First TD"] == "—"
    assert wk2["Week $"] == pytest.approx(9.09 * 2 - 10, abs=0.02)   # unpriced safe plays add nothing


def test_position_scorecard():
    sc = g["compute_position_scorecard"](g["filter_picks_by_season"](PICKS, 2026))
    wr = sc[(sc["category"] == "Prop Edge") & (sc["position"] == "WR")].iloc[0]
    assert wr["record"] == "2-1-1" and wr["hit_pct"] == pytest.approx(66.67, abs=0.01)
    assert wr["tier"] == "new"                                    # under 5 picks
    rb = sc[(sc["category"] == "Prop Edge") & (sc["position"] == "RB")].iloc[0]
    assert rb["record"] == "1-0"
    # ordered by category then QB/RB/WR/TE
    assert list(sc["category"])[:2] == ["Prop Edge", "Prop Edge"]
    assert list(sc[sc["category"] == "Prop Edge"]["position"]) == ["RB", "WR"]
    assert (sc[sc["category"] == "Safe Play"]["tier"] == "new").all()   # Safe Plays get their own badge


def test_empty_inputs():
    assert g["compute_running_by_week"]([]).empty
    assert g["compute_weekly_table"]([]).empty
    assert g["compute_position_scorecard"]([]).empty
    assert g["compute_season_summary"]([]).empty
