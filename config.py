"""
config.py
"""

# Seasons to pull weekly stats for. CURRENT_SEASON drives which season's
# numbers show up in the Overview cards and Prop Comparator (since those
# are about right-now decisions); every season in SEASONS shows up in the
# Player Deep Dive trend charts for longer-term context.
SEASONS = [2025, 2026]
CURRENT_SEASON = max(SEASONS)

# How many players per position count as "starters" to auto-track.
# e.g. WR: 2 means each team's top 2 depth-chart wide receivers.
STARTERS_PER_POSITION = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
}

# Where the local database file lives
DB_PATH = "data/fantasy.db"

# Maps our internal stat column names to The Odds API's player-prop
# market keys, for the positions where that stat is the primary one.
# Only stats with a mapping here get a prop-line comparison on cards.
# NOTE: rushing_tds/receiving_tds are deliberately NOT mapped - The Odds
# API doesn't offer them as separate Over/Under lines. Rushing and
# receiving touchdowns only show up bundled into "anytime touchdown
# scorer" (a yes/no market, not a point value), which doesn't fit this
# app's point-vs-average comparison. player_pass_tds is real and works.
# The one real market that DOES cover rushing/receiving touchdowns: a
# yes/no "does this player score at all" market with betting odds, not a
# point value - handled separately from PROP_MARKET_MAP (see
# load_prop_lines / get_anytime_td_odds) since it can't be compared to a
# season average the same way.
ANYTIME_TD_MARKET = "player_anytime_td"

# Safety margin, in Odds API usage credits, to always keep in reserve.
# Before spending anything on player-prop odds, the app checks the
# quota-remaining count the API reports and skips pulling odds entirely
# if doing so would eat into this buffer - protects against ever running
# the account down to zero, at the cost of that day's refresh being empty
# instead (it'll try again on the next cached refresh, up to 24h later).
ODDS_API_SAFETY_BUFFER = 500

PROP_MARKET_MAP = {
    "passing_yards": "player_pass_yds",
    "rushing_yards": "player_rush_yds",
    "receiving_yards": "player_reception_yds",
    "passing_tds": "player_pass_tds",
}

# Home city for each team's stadium, used to look up game-day weather via
# Open-Meteo's free geocoding + forecast APIs (no API key required). These
# are metro-area names, not exact stadium addresses - plenty precise for
# weather purposes. Teams that share a stadium (NYG/NYJ) share a city.
TEAM_CITY = {
    "ARI": "Glendale, AZ",
    "ATL": "Atlanta, GA",
    "BAL": "Baltimore, MD",
    "BUF": "Orchard Park, NY",
    "CAR": "Charlotte, NC",
    "CHI": "Chicago, IL",
    "CIN": "Cincinnati, OH",
    "CLE": "Cleveland, OH",
    "DAL": "Arlington, TX",
    "DEN": "Denver, CO",
    "DET": "Detroit, MI",
    "GB": "Green Bay, WI",
    "HOU": "Houston, TX",
    "IND": "Indianapolis, IN",
    "JAX": "Jacksonville, FL",
    "KC": "Kansas City, MO",
    "LA": "Inglewood, CA",
    "LAC": "Inglewood, CA",
    "LV": "Las Vegas, NV",
    "MIA": "Miami Gardens, FL",
    "MIN": "Minneapolis, MN",
    "NE": "Foxborough, MA",
    "NO": "New Orleans, LA",
    "NYG": "East Rutherford, NJ",
    "NYJ": "East Rutherford, NJ",
    "PHI": "Philadelphia, PA",
    "PIT": "Pittsburgh, PA",
    "SEA": "Seattle, WA",
    "SF": "Santa Clara, CA",
    "TB": "Tampa, FL",
    "TEN": "Nashville, TN",
    "WAS": "Landover, MD",
}

# Roof states (from the schedule's own 'roof' column) where fetching
# weather doesn't make sense - the game isn't exposed to the elements.
INDOOR_ROOF_STATES = {"dome", "closed"}
