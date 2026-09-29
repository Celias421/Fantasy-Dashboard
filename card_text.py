"""
card_text.py
Plain-English wording for the player cards (Sep 2026).

Every card says three things in the same order: the bet, one sentence on
why, then a short checklist of reasons. Each reason is a full sentence
with a tone ("good" / "bad" / "neutral" / "info") that sets its marker
(✓ / ! / – / •) and color - the words always carry the meaning on their
own, so nobody has to know what a number means to read a card.

All functions here are plain Python (no Streamlit), so they're unit-tested
in tests/test_card_text.py.
"""

import html
import re

from odds_math import book_name

MARKER = {"good": "✓", "bad": "!", "neutral": "–", "info": "•"}

STAT_WORDS = {
    "passing_yards": "passing yards", "passing_tds": "passing TDs", "rushing_yards": "rushing yards",
    "rushing_tds": "rushing TDs", "receiving_yards": "receiving yards", "receptions": "catches",
    "receiving_tds": "receiving TDs", "fantasy_points_ppr": "fantasy points (PPR)",
    "fantasy_points": "fantasy points", "targets": "targets", "carries": "carries",
    "completions": "completions", "attempts": "pass attempts", "passing_interceptions": "interceptions",
}
POSITION_PLURAL = {"QB": "QBs", "RB": "RBs", "WR": "WRs", "TE": "TEs"}
CATEGORY_WORDS = {"edge": "prop-edge", "td_anytime": "anytime-TD", "td_first": "first-TD", "safe": "Safe Play"}


def stat_words(stat_col: str) -> str:
    return STAT_WORDS.get(stat_col, str(stat_col).replace("_", " "))


def ordinal(n: int) -> str:
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _fmt(x: float) -> str:
    """52.0 -> '52', 52.3 -> '52.3'."""
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}"


# ---------- HTML building blocks ----------
def reason_html(tone: str, text: str) -> str:
    """One checklist line. `text` may contain simple inline HTML (<b>)."""
    return (f'<div class="why why-{tone}"><span class="why-mark">{MARKER[tone]}</span>'
            f'<span class="why-text">{text}</span></div>')


def reasons_html(reasons) -> str:
    """reasons: iterable of (tone, text) or None - Nones are skipped."""
    rows = [reason_html(t, x) for r in reasons if r for t, x in [r]]
    return f'<div class="why-list">{"".join(rows)}</div>' if rows else ""


def bet_html(direction: str, line: float, stat_col: str, note: str = "") -> str:
    """'▲ Over 45.5 receiving yards' headline, green for Over, red for Under."""
    over = "Over" in direction
    cls = "bet-over" if over else "bet-under"
    arrow = "▲" if over else "▼"
    note_html = f' <span class="bet-note">{html.escape(note)}</span>' if note else ""
    return (f'<div class="bet-line {cls}">{arrow} {"Over" if over else "Under"} {_fmt(line)} '
            f'{stat_words(stat_col)}{note_html}</div>')


def headline_html(text: str, cls: str = "bet-td") -> str:
    return f'<div class="bet-line {cls}">{text}</div>'


def sub_html(text: str) -> str:
    return f'<div class="bet-sub">{text}</div>'


# ---------- sentences ----------
def edge_sentence(avg: float, line: float, stat_col: str, season: int) -> str:
    gap = avg - line
    more = "more" if gap > 0 else "less"
    if stat_col in ("receptions",):
        more = "more" if gap > 0 else "fewer"
    return (f"He's averaging <b>{_fmt(avg)}</b> {stat_words(stat_col)} this season "
            f"({season}) — <b>{_fmt(abs(gap))} {more}</b> than the line.")


def line_vs_avg_sentence(avg: float, line, has_prop: bool, stat_col: str):
    """For stat cards (Research, Game Center): how his average compares
    to the sportsbook line. Returns (tone, text)."""
    if not has_prop or line is None:
        return ("info", "No sportsbook line posted for this stat yet.")
    gap = avg - line
    if abs(gap) <= 0.5:
        return ("neutral", f"Sportsbook line is <b>{_fmt(line)}</b> — right at his average.")
    word = "above" if gap > 0 else "below"
    return ("good" if gap > 0 else "bad",
            f"Sportsbook line is <b>{_fmt(line)}</b> — he's averaging {_fmt(abs(gap))} {word} it.")


def price_reason(ev, price, book):
    """EV at the best price, in cents per dollar."""
    if ev is None or price is None:
        return None
    cents = ev * 100
    where = f"<b>{int(price):+d}</b> at {html.escape(book_name(book))}" if book else f"<b>{int(price):+d}</b>"
    if cents >= 2:
        return ("good", f"Good price: {where} — worth about {cents:.0f}¢ per $1 bet over time.")
    if cents <= -2:
        return ("bad", f"Pricey: {where} — the sportsbook's cut outweighs the chance (about "
                       f"{abs(cents):.0f}¢ lost per $1 over time).")
    return ("neutral", f"Fair price: {where} — about break-even over time.")


def game_reason(team: str, implied_total, high: float = 26, low: float = 19):
    if implied_total is None or implied_total != implied_total:  # NaN check
        return None
    pts = f"<b>{implied_total:.1f} points</b>"
    if implied_total >= high:
        return ("good", f"High-scoring game expected: Vegas has {team} scoring {pts}.")
    if implied_total <= low:
        return ("bad", f"Low-scoring game expected: Vegas has {team} scoring only {pts}.")
    return ("neutral", f"Average scoring game: Vegas has {team} scoring {pts}.")


def role_reason(trend):
    """compute_opportunity_trend's dict -> sentence (None when steady/unknown)."""
    if not trend or trend.get("tier") == "stable":
        return None
    what = "share of the team's targets" if trend.get("metric") == "Target Share" else "share of the snaps"
    recent, season = trend["recent_pct"], trend["season_pct"]
    if trend["tier"] == "up":
        return ("good", f"Bigger role lately: <b>{recent:.0f}%</b> {what} in recent games, "
                        f"up from {season:.0f}% on the season.")
    return ("bad", f"Smaller role lately: <b>{recent:.0f}%</b> {what} in recent games, "
                   f"down from {season:.0f}% on the season.")


def record_reason(segment, category: str, position: str, min_n: int):
    """Our Track Record for this kind of pick at this position."""
    kind = f"{CATEGORY_WORDS.get(category, category)} picks on {POSITION_PLURAL.get(position, position)}"
    if not segment:
        return ("neutral", f"Our {kind}: no results yet this season.")
    n, pct = segment["n"], segment["pct"]
    hits = round(pct * n / 100)
    rec = f"<b>{hits} of {n}</b> hit"
    if segment["tier"] == "new":
        return ("neutral", f"Our {kind}: {rec} so far — too early to judge (need {min_n}+).")
    if segment["tier"] == "high":
        return ("good", f"Our {kind} are hot: {rec} ({pct:.0f}%).")
    if segment["tier"] == "low":
        return ("bad", f"Our {kind} are cold: only {rec} ({pct:.0f}%).")
    return ("neutral", f"Our {kind}: {rec} ({pct:.0f}%) — about even.")


def td_reason(pct, first: bool = False):
    if pct is None or pct != pct:
        return None
    if first:
        return ("info", f"Sportsbooks give him a <b>{pct:.0f}%</b> chance to score the game's first TD.")
    return ("info", f"Sportsbooks give him a <b>{pct:.0f}%</b> chance to score a TD.")


def matchup_reason(label, rank, position: str, max_rank: int = 32):
    """rank: opponent defense vs this position/stat, 1 = toughest, 32 = easiest.
    label: _matchup_label text ('🏠 vs DAL (Sun 1:00 PM) — #28 toughest')."""
    if label is None or rank is None or rank != rank:
        return None
    where = re.sub(r"\s*\([^)]*\)\s*$", "", str(label).split(" — ")[0])  # drop the kickoff time
    rank = int(rank)
    pos = POSITION_PLURAL.get(position, position)
    if rank >= max_rank - 9:
        return ("good", f"Easy matchup {where}: the {ordinal(max_rank + 1 - rank)}-easiest defense vs {pos}.")
    if rank <= 10:
        return ("bad", f"Tough matchup {where}: the {ordinal(rank)}-toughest defense vs {pos}.")
    return ("neutral", f"Middle-of-the-pack matchup {where} ({ordinal(rank)} toughest of {max_rank}).")


def consistency_reason(label):
    return {
        "High": ("good", "Steady: his week-to-week numbers rarely swing much."),
        "Medium": ("neutral", "Somewhat steady week to week."),
        "Low": ("bad", "Up-and-down: big weeks and quiet weeks."),
    }.get(label)


def injury_reason(label):
    if not label or label != label:
        return None
    return ("bad", f"On the injury report: <b>{html.escape(str(label))}</b>.")


def safe_sentence(hits: int, n: int, break_even: float, price: int) -> str:
    return (f"Cleared this line in <b>{hits} of his last {n}</b> games. At {int(price):+d} it needs to win "
            f"{break_even * 100:.0f}% of the time to break even.")


# ---------- table cells (Sep 2026) ----------
# Short, plain-text versions of the card sentences for st.dataframe cells
# (which show text, not HTML). Same markers as the cards: ✓ helps the
# bet, ! hurts it, – neutral. Missing data is "—".
CELL_MARK = {"good": "✓", "bad": "!", "neutral": "–", "info": ""}


def _cell(tone: str, text: str) -> str:
    mark = CELL_MARK.get(tone, "")
    return f"{mark} {text}".strip()


def bet_text(direction: str, line: float, stat_col: str) -> str:
    over = "Over" in direction
    return f'{"▲ Over" if over else "▼ Under"} {_fmt(line)} {stat_words(stat_col)}'


def avg_vs_line_cell(avg: float, line: float) -> str:
    gap = avg - line
    if abs(gap) < 0.05:
        return "even"
    return f"{_fmt(abs(gap))} {'above' if gap > 0 else 'below'}"


def price_cell(ev, price, book) -> str:
    if ev is None or price is None:
        return "—"
    cents = ev * 100
    where = f"{int(price):+d} {book_name(book)}" if book else f"{int(price):+d}"
    if cents >= 2:
        return _cell("good", f"Good: {where}, +{cents:.0f}¢/$1")
    if cents <= -2:
        return _cell("bad", f"Pricey: {where}, −{abs(cents):.0f}¢/$1")
    return _cell("neutral", f"Fair: {where}")


def game_cell(implied_total, high: float = 26, low: float = 19) -> str:
    if implied_total is None or implied_total != implied_total:
        return "—"
    if implied_total >= high:
        return _cell("good", f"High ({implied_total:.1f} pts)")
    if implied_total <= low:
        return _cell("bad", f"Low ({implied_total:.1f} pts)")
    return _cell("neutral", f"Average ({implied_total:.1f} pts)")


def role_cell(trend) -> str:
    if not trend:
        return "—"
    what = "targets" if trend.get("metric") == "Target Share" else "snaps"
    recent, season = trend["recent_pct"], trend["season_pct"]
    if trend["tier"] == "up":
        return _cell("good", f"Bigger: {recent:.0f}% of {what}, was {season:.0f}%")
    if trend["tier"] == "down":
        return _cell("bad", f"Smaller: {recent:.0f}% of {what}, was {season:.0f}%")
    return _cell("neutral", "Steady")


def record_cell(segment, min_n: int) -> str:
    if not segment:
        return "No results yet"
    n, pct = segment["n"], segment["pct"]
    hits = round(pct * n / 100)
    if segment["tier"] == "new":
        return f"Too early: {hits} of {n}"
    label = {"high": ("good", "Hot"), "low": ("bad", "Cold"), "neutral": ("neutral", "Even")}[segment["tier"]]
    return _cell(label[0], f"{label[1]}: {hits} of {n} ({pct:.0f}%)")


def matchup_cell(rank, position: str, max_rank: int = 32) -> str:
    """rank 1 = toughest defense vs this position, 32 = easiest."""
    if rank is None or rank != rank or rank == "":
        return "—"
    rank = int(rank)
    pos = POSITION_PLURAL.get(position, position)
    if rank >= max_rank - 9:
        return _cell("good", f"Easy: {ordinal(max_rank + 1 - rank)}-easiest vs {pos}")
    if rank <= 10:
        return _cell("bad", f"Tough: {ordinal(rank)}-toughest vs {pos}")
    return _cell("neutral", f"Average: {ordinal(rank)} toughest of {max_rank}")


def line_move_cell(clv, unit: str) -> str:
    """Closing Line Value in words: did the line move toward our pick?"""
    if clv is None:
        return "—"
    units = "pts" if unit == "pts" else "% chance"
    if abs(clv) < 0.05:
        return "– Didn't move"
    if clv > 0:
        return _cell("good", f"Moved our way ({clv:+.1f} {units})")
    return _cell("bad", f"Moved against us ({clv:+.1f} {units})")
