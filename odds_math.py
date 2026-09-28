"""
odds_math.py
Pure betting math - no Streamlit, no network - so it can be unit tested
on its own and shared by data_loader.py (building the odds tables) and
dashboard.py (EV and profit on screen).

Terms, in plain English:
- American odds: the "-110" / "+150" format sportsbooks show.
- Decimal odds: total payout per $1 staked, stake included (-110 -> 1.909).
- Break-even %: how often a bet at that price has to win just to not lose
  money. It's the price's implied probability: -110 -> 52.4%.
- Fair probability: the betting market's estimate of the TRUE chance, with
  the sportsbooks' built-in profit ("vig") removed.
- Expected value (EV): average profit per $1 bet if the fair probability
  is right. +0.05 = you'd expect 5 cents back per dollar over the long run.
"""

from statistics import median

# Odds API bookmaker keys -> the names people actually know them by.
BOOK_NAMES = {
    "draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM",
    "williamhill_us": "Caesars", "caesars": "Caesars", "betrivers": "BetRivers",
    "espnbet": "ESPN BET", "fanatics": "Fanatics", "bovada": "Bovada",
    "betonlineag": "BetOnline", "mybookieag": "MyBookie", "lowvig": "LowVig",
    "betus": "BetUS", "hardrockbet": "Hard Rock", "ballybet": "Bally Bet",
    "pointsbetus": "PointsBet", "unibet_us": "Unibet", "superbook": "SuperBook",
    "wynnbet": "WynnBET", "betparx": "betPARX", "fliff": "Fliff",
}


def book_name(key) -> str:
    if not key:
        return ""
    return BOOK_NAMES.get(key, str(key).replace("_", " ").title())


def american_to_decimal(american: float) -> float:
    a = float(american)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / -a)


def decimal_to_american(decimal: float) -> int:
    d = float(decimal)
    if d >= 2.0:
        return int(round((d - 1.0) * 100.0))
    return int(round(-100.0 / (d - 1.0)))


def break_even_prob(american: float) -> float:
    """Win rate needed to break even at this price (0-1)."""
    a = float(american)
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def expected_value(fair_prob: float, american: float) -> float:
    """Average profit per $1 staked if fair_prob is the true win chance."""
    return float(fair_prob) * american_to_decimal(american) - 1.0


def profit_on_stake(stake: float, american: float, result: str) -> float:
    """Profit (or loss) of one flat bet: Hit pays at the price, Miss loses
    the stake, Push returns it. Anything else (Pending) is 0."""
    if result == "Hit":
        return stake * (american_to_decimal(american) - 1.0)
    if result == "Miss":
        return -stake
    return 0.0


def _valid_price(p) -> bool:
    try:
        return p is not None and abs(float(p)) >= 100
    except (TypeError, ValueError):
        return False


def typical_price(prices) -> int | None:
    """The middle price across books (median in decimal terms, converted
    back to American) - what you'd get at an average book, not the best."""
    decs = [american_to_decimal(p) for p in prices if _valid_price(p)]
    return decimal_to_american(median(decs)) if decs else None


def best_price(prices_by_book: dict):
    """(best American price, book key) - the book that pays the most."""
    valid = {b: p for b, p in prices_by_book.items() if _valid_price(p)}
    if not valid:
        return None, None
    book = max(valid, key=lambda b: american_to_decimal(valid[b]))
    return int(round(float(valid[book]))), book


def devig_two_way(over_price, under_price):
    """Fair Over probability from one book's Over/Under prices, or None."""
    if not (_valid_price(over_price) and _valid_price(under_price)):
        return None
    po, pu = break_even_prob(over_price), break_even_prob(under_price)
    return po / (po + pu) if po + pu > 0 else None


def summarize_prop_books(books: list) -> dict | None:
    """Collapse every sportsbook's version of ONE player prop into what the
    app shows. `books` = [{"book", "point", "over_price", "under_price"}].

    - line: the line the most books are offering (a real, bettable line -
      never an average of different books' lines, which can produce a
      number no book actually offers). Ties go to the line closest to the
      middle of all lines, then the lower one, so the pick is stable.
    - fair_prob_over: average de-vigged Over chance across books AT that
      line (books offering a different line are describing a different bet).
    - best/typical prices for each side, from books at that line.
    """
    rows = [b for b in books if b.get("point") is not None]
    if not rows:
        return None
    counts = {}
    for b in rows:
        counts[b["point"]] = counts.get(b["point"], 0) + 1
    top = max(counts.values())
    candidates = [pt for pt, n in counts.items() if n == top]
    mid = median([b["point"] for b in rows])
    line = min(candidates, key=lambda pt: (abs(pt - mid), pt))
    at_line = [b for b in rows if b["point"] == line]

    fairs = [f for f in (devig_two_way(b.get("over_price"), b.get("under_price")) for b in at_line) if f is not None]
    over_best, over_book = best_price({b["book"]: b.get("over_price") for b in at_line})
    under_best, under_book = best_price({b["book"]: b.get("under_price") for b in at_line})
    return {
        "point": line,
        "fair_prob_over": sum(fairs) / len(fairs) if fairs else None,
        "n_books": len(at_line),
        "best_over_price": over_best, "best_over_book": over_book,
        "best_under_price": under_best, "best_under_book": under_book,
        "typical_over_price": typical_price([b.get("over_price") for b in at_line]),
        "typical_under_price": typical_price([b.get("under_price") for b in at_line]),
    }
