"""
slip_parser.py
Best-effort OCR + heuristic field extraction for a bet-slip screenshot -
the "pre-fill" behind the Bet Slip Tracker's upload form.

Deliberately free/local (pytesseract -> the system tesseract-ocr binary)
rather than a paid vision-API call, per how this feature was scoped: no
API key, no per-slip cost, works offline. The tradeoff is accuracy -
sportsbook apps use a lot of custom fonts/icons that plain OCR mangles -
so every field this module extracts is a STARTING GUESS shown in an
editable form, never saved as-is. parse_slip_text()'s regexes are
intentionally generous (favor a wrong-but-present guess a user can
correct in one click over silently leaving a field blank).
"""

import re

from slip_store import BET_TYPES, SPORTSBOOKS

# Matches American odds: +450, -110, occasionally with a stray space
# after the sign (some OCR passes insert one).
_ODDS_RE = re.compile(r"([+\-])\s?(\d{2,5})")

_STAKE_RE = re.compile(
    r"(?:wager|risk|bet\s*amount|stake)\D{0,6}\$?\s*([\d,]+\.?\d{0,2})", re.IGNORECASE,
)
_PAYOUT_RE = re.compile(
    r"(?:to\s*win|total\s*payout|potential\s*(?:cash\s*out|payout|win)|payout)\D{0,6}\$?\s*([\d,]+\.?\d{0,2})",
    re.IGNORECASE,
)
# A line that looks like a prop/spread leg: has a number and one of the
# usual sportsbook-app words for a bet line.
_LEG_LINE_RE = re.compile(
    r"\b(over|under|o\d|u\d|spread|moneyline|ml|\+\d|\-\d|to score|anytime td|passing|rushing|receiving|points?|yards?|assists?|rebounds?)\b",
    re.IGNORECASE,
)


def ocr_image_to_text(image_bytes: bytes) -> str:
    """Raw OCR text from an uploaded screenshot's bytes, or "" if OCR
    isn't available (tesseract missing) or the image can't be read -
    never raises, so a bad/corrupt upload just means an empty pre-fill
    instead of a crashed tab."""
    try:
        import io
        from PIL import Image
        import pytesseract

        image = Image.open(io.BytesIO(image_bytes))
        # Upscaling small screenshots and converting to grayscale both
        # measurably improve tesseract's accuracy on UI screenshots (as
        # opposed to scanned documents, which is what it's tuned for).
        if image.width < 900:
            scale = 900 / image.width
            image = image.resize((int(image.width * scale), int(image.height * scale)))
        image = image.convert("L")
        return pytesseract.image_to_string(image)
    except Exception:
        return ""


def _clean_amount(raw: str) -> float:
    try:
        return float(raw.replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def parse_slip_text(text: str) -> dict:
    """Heuristic best-guess fields from raw OCR text. Every value is a
    starting point for the review form, not a final answer - callers
    should always let the user see/edit every field before saving."""
    lower = text.lower()

    sportsbook = next((b for b in SPORTSBOOKS if b.lower() in lower), None)

    if "same game parlay" in lower or "sgp" in lower:
        bet_type = "Same Game Parlay"
    elif "parlay" in lower:
        bet_type = "Parlay"
    elif "teaser" in lower:
        bet_type = "Teaser"
    elif "round robin" in lower:
        bet_type = "Round Robin"
    else:
        bet_type = "Single"

    stake_match = _STAKE_RE.search(text)
    stake = _clean_amount(stake_match.group(1)) if stake_match else 0.0

    payout_match = _PAYOUT_RE.search(text)
    payout = _clean_amount(payout_match.group(1)) if payout_match else 0.0

    odds_matches = _ODDS_RE.findall(text)
    if odds_matches:
        # A parlay/teaser/round-robin slip usually lists each leg's own
        # odds first and the combined odds for the whole ticket last
        # (right above the wager/payout line) - a single-bet slip only
        # has the one number either way, so "last" is a safe pick there
        # too. This is still just a guess, shown in an editable field.
        sign, digits = odds_matches[-1] if bet_type != "Single" else odds_matches[0]
        odds = f"{sign}{digits}"
    else:
        odds = ""

    legs = [
        line.strip() for line in text.splitlines()
        if line.strip() and _LEG_LINE_RE.search(line) and len(line.strip()) < 120
    ]
    # De-dupe while preserving order (OCR sometimes repeats a line it
    # picked up from both the card and a subtotal footer).
    seen = set()
    legs = [l for l in legs if not (l in seen or seen.add(l))]

    return {
        "sportsbook": sportsbook or "Other",
        "bet_type": bet_type if bet_type in BET_TYPES else "Other",
        "stake": stake,
        "potential_payout": payout,
        "odds": odds,
        "legs": legs,
    }
