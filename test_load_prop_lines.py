"""load_prop_lines against a fake Odds API response (no network, no credits)."""

import data_loader


class _Resp:
    def __init__(self, payload):
        self._p = payload
        self.headers = {"x-requests-remaining": "15000", "x-requests-used": "5000"}

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _book(key, point, over, under, td_yes, first_yes, alt_49=-300):
    return {"key": key, "markets": [
        {"key": "player_rush_yds", "outcomes": [
            {"name": "Over", "description": "Saquon Barkley", "point": point, "price": over},
            {"name": "Under", "description": "Saquon Barkley", "point": point, "price": under},
        ]},
        {"key": "player_anytime_td", "outcomes": [
            {"name": "Yes", "description": "Saquon Barkley", "price": td_yes},
        ]},
        {"key": "player_rush_yds_alternate", "outcomes": [
            {"name": "Over", "description": "Saquon Barkley", "point": 49.5, "price": alt_49},
            {"name": "Over", "description": "Saquon Barkley", "point": 59.5, "price": -190},
        ]},
        {"key": "player_1st_td", "outcomes": [
            {"name": "Yes", "description": "Saquon Barkley", "price": first_yes},
            {"name": "Yes", "description": "Jalen Hurts", "price": 900},
        ]},
    ]}


EVENT_ODDS = {"bookmakers": [
    _book("draftkings", 75.5, -110, -110, -120, 450),
    _book("fanduel", 75.5, -105, -115, -110, 500, alt_49=-260),
    _book("betmgm", 76.5, -120, 100, -125, 475),
]}


def _fake_get(url, params=None, timeout=None):
    if url.endswith("/events/"):
        return _Resp([{"id": "evt1"}])
    return _Resp(EVENT_ODDS)


def test_real_line_and_prices(monkeypatch):
    monkeypatch.setattr(data_loader.requests, "get", _fake_get)
    props, td, first_td, quota, alt = data_loader.load_prop_lines("fake-key")

    row = props.iloc[0]
    assert list(props.columns) == data_loader.PROP_COLUMNS
    assert row["point"] == 75.5                      # most books' real line, not 75.83
    assert row["n_books"] == 2
    assert row["best_over_price"] == -105 and row["best_over_book"] == "fanduel"
    assert row["best_under_price"] == -110 and row["best_under_book"] == "draftkings"
    assert 0.49 < row["fair_prob_over"] < 0.52

    t = td[td["player"] == "Saquon Barkley"].iloc[0]
    assert t["best_price"] == -110 and t["best_book"] == "fanduel"
    assert t["typical_price"] == -120
    assert 50 < t["implied_prob"] < 56                # unchanged meaning: avg implied %, vig included

    f = first_td[first_td["player"] == "Saquon Barkley"].iloc[0]
    assert f["best_price"] == 500 and f["best_book"] == "fanduel"


def test_cache_reader_tolerates_old_schema(tmp_path, monkeypatch):
    import json, datetime
    path = tmp_path / "cache.json"
    json.dump({
        "props": [{"player": "X", "market": "player_rush_yds", "point": 50.5, "fair_prob_over": 0.5}],
        "td": [{"player": "X", "implied_prob": 40.0}],
        "quota": {"remaining": 1, "used": 1, "skipped": False},
        "pulled_at": datetime.datetime.now().isoformat(),
    }, open(path, "w"))
    monkeypatch.setattr(data_loader, "PROP_LINES_CACHE_PATH", str(path))
    cached = data_loader._read_prop_lines_cache_file()
    assert cached is not None
    assert list(cached["props_df"].columns) == data_loader.PROP_COLUMNS
    assert cached["props_df"]["best_over_price"].isna().all()
    assert list(cached["td_df"].columns) == data_loader.TD_COLUMNS


def test_alternate_lines(monkeypatch):
    monkeypatch.setattr(data_loader.requests, "get", _fake_get)
    _, _, _, _, alt = data_loader.load_prop_lines("fake-key")
    assert list(alt.columns) == data_loader.ALT_COLUMNS
    row = alt[(alt["point"] == 49.5)].iloc[0]
    assert row["market"] == "player_rush_yds" and row["side"] == "Over"      # suffix stripped
    assert row["n_books"] == 3 and row["best_price"] == -260 and row["best_book"] == "fanduel"
    assert row["typical_price"] == -300
    assert len(alt) == 2
