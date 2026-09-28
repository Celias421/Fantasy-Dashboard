"""
theme.py
The Prop Shop's visual identity - brand colors, the global CSS that skins
Streamlit's native widgets to match, and the wordmark header shown on every
page. One module so every part of the app pulls from the same palette
instead of each feature inventing its own colors (the standing rule this
codebase already follows for behavior applies to styling too).

Brand system (v2, Sep 2026 redesign) - replaces the original lime-green
logo/palette:
  * Ground: warm near-black, not pure black - reads as deliberate, cinematic.
  * ONE signature hue: deep brass/gold (#D4A934) - scoreboard/trophy-room
    feel. Used sparingly for brand moments: the wordmark dot, active nav
    state, primary buttons, headline metric values, the header rule.
  * Status colors are SEPARATE from the brand hue: GOOD (green) for hits /
    upward deltas / favorable, BAD (red) for misses / downward / tough,
    WARN (amber) for pending. The old palette conflated "brand" and "good"
    because the brand color happened to be green; with a gold brand that
    would make every Hit badge read as decoration instead of success, so
    they're split.
  * Position colors (QB blood red, RB navy, WR rust, TE emerald) - fixed
    identity colors, validated for color-vision-deficiency separation and
    contrast against the dark surface before being adopted.
  * Type: Big Shoulders Display (condensed, scoreboard-weight) for the
    wordmark, headings and big numbers; Manrope for body/UI; IBM Plex Mono
    for tabular data so figures line up.
"""

import streamlit as st

# ---- Brand palette -----------------------------------------------------
# Core surfaces / ink
BG = "#0B0A08"               # app background - warm near-black
SURFACE = "#16140F"          # cards, sidebar, secondary background
SURFACE_2 = "#1F1C15"        # nested/inset surfaces (inputs, chips)
LINE = "#332C1D"             # borders/dividers - warm, not gray
INK = "#F6F3EA"              # primary text - warm off-white
SUB = "#A39B8A"              # secondary/muted text

# Brand accent - brass/gold
ACCENT = "#D4A934"
ACCENT_HOVER = "#B8901F"
ACCENT_INK = "#241A05"       # text color ON a solid-accent background
ACCENT_SOFT = "rgba(212, 169, 52, 0.14)"         # badge/chip fills
ACCENT_SOFT_STRONG = "rgba(212, 169, 52, 0.30)"

# Semantic status (separate from the brand accent - meaning, not decoration)
GOOD = "#43C283"
GOOD_SOFT = "rgba(67, 194, 131, 0.14)"
GOOD_BORDER = "rgba(67, 194, 131, 0.32)"
BAD = "#E5555A"
BAD_SOFT = "rgba(229, 85, 90, 0.14)"
BAD_BORDER = "rgba(229, 85, 90, 0.32)"
WARN = "#F5B324"
WARN_SOFT = "rgba(245, 179, 36, 0.14)"
WARN_BORDER = "rgba(245, 179, 36, 0.32)"

# Position identity colors - single source of truth. dashboard.py's
# POSITION_COLORS points here rather than keeping its own copy. Passed a
# colorblind-safety check (lightness band, chroma floor, CVD separation,
# normal-vision separation, contrast vs the dark surface) as a set.
POSITION_COLORS = {
    "QB": "#C23B34",   # blood red
    "RB": "#4A66A8",   # navy
    "WR": "#C1622C",   # burnt rust
    "TE": "#1F8F62",   # emerald
}

# Pick-category identity colors (Track Record charts, TD Chances chart) -
# deliberately NOT borrowed from POSITION_COLORS: under this palette that
# would paint "Prop Edge" blood red on a hit-rate chart, which reads as
# "miss". Its own validated set (same checks as the position colors), and
# the same category wears the same color everywhere in the app.
CATEGORY_COLORS = {
    "Prop Edge": "#B08820",    # deep gold
    "Anytime TD": "#2E9E90",   # teal
    "First TD": "#8266D4",     # violet
}

# Matchup-difficulty gradient endpoints (toughest -> easiest), used by
# matchup_rank_color() in dashboard.py. Anchored to the status colors
# (BAD -> GOOD) rather than the brand accent, since "tough vs easy
# matchup" is a semantic judgment, not a brand moment.
MATCHUP_TOUGH_RGB = (229, 85, 90)
MATCHUP_EASY_RGB = (67, 194, 131)
# Midpoint of every red -> amber -> green badge gradient in the app
# (matchup difficulty, implied totals, fair prob, TD probability, weather
# risk) - same amber as WARN, so the whole ramp is built from status colors.
RAMP_MID_RGB = (245, 179, 36)

FONT_DISPLAY = "'Big Shoulders Display', 'Arial Narrow', system-ui, sans-serif"
FONT_BODY = "'Manrope', system-ui, -apple-system, 'Segoe UI', sans-serif"
FONT_MONO = "'IBM Plex Mono', ui-monospace, 'SFMono-Regular', monospace"

_GOOGLE_FONTS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Big+Shoulders+Display:wght@600;700;800;900"
    "&family=Manrope:wght@400;500;600;700;800"
    "&family=IBM+Plex+Mono:wght@400;500;600"
    "&display=swap"
)


def inject_css() -> None:
    """Global CSS: recolors this app's own custom classes (player-card,
    badges, deltas) AND overrides Streamlit's native widgets (buttons,
    tabs, metrics, dataframes, forms, expanders, alerts, the site-section
    radio) via their data-testid attributes, which are far more stable
    across Streamlit versions than its internal CSS class names. Also loads
    the brand fonts - previously the CSS named "IBM Plex Sans" but never
    actually loaded it, so every browser silently fell back to a system
    font. Call once, right after st.set_page_config()."""
    # IMPORTANT: this string must START with <style>. st.markdown runs the
    # text through a markdown parser first; a block opening with <style> is
    # kept intact until </style> even across blank lines, but a block
    # opening with anything else (e.g. a <link> tag) ends at the FIRST
    # blank line - everything after it gets rendered as visible paragraph
    # text and none of those rules apply. Fonts load via @import inside
    # the style block for exactly that reason.
    st.markdown(f"""
    <style>
    @import url('{_GOOGLE_FONTS_URL}');

    /* ---- Base type & background -------------------------------------- */
    html, body, [class*="css"], .stApp, .stMarkdown, button, input, textarea, select {{
        font-family: {FONT_BODY};
    }}
    .stApp {{
        background: {BG};
    }}
    ::selection {{ background: {ACCENT_SOFT_STRONG}; color: {INK}; }}

    /* ---- Global type scale -------------------------------------------
       This app is used mostly on laptop/desktop monitors, so text leans
       larger than Streamlit's own defaults across the board. Nothing a
       user reads renders below ~13px. */
    html, body {{ font-size: 17px; }}
    [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li,
    [data-testid="stMarkdownContainer"] span {{
        font-size: 1rem;
    }}
    [data-testid="stWidgetLabel"] p, label {{
        font-size: 1rem !important;
    }}
    /* ...but the rule above must NOT reach inside headings or metric
       values: Streamlit wraps heading text in an inner <span> and metric
       values in an inner <p>, so without this exemption both render at
       body size even though their containers are sized large. */
    [data-testid="stMarkdownContainer"] h1 span, [data-testid="stMarkdownContainer"] h2 span,
    [data-testid="stMarkdownContainer"] h3 span, [data-testid="stMarkdownContainer"] h4 span,
    [data-testid="stMarkdownContainer"] h5 span,
    [data-testid="stHeading"] span,
    [data-testid="stMetricValue"] p, [data-testid="stMetricValue"] span,
    [data-testid="stMetricValue"] div {{
        font-size: inherit !important;
        font-family: inherit !important;
        font-weight: inherit !important;
        line-height: inherit !important;
    }}
    .stSelectbox div, .stMultiSelect div, .stTextInput input, .stNumberInput input {{
        font-size: 0.95rem;
    }}

    /* Headings: condensed scoreboard display face. Covers st.title /
       st.header / st.subheader and markdown #/##/### headings. */
    h1, h2, h3, h4, h5,
    [data-testid="stHeading"] h1, [data-testid="stHeading"] h2,
    [data-testid="stHeading"] h3, [data-testid="stHeading"] h4,
    [data-testid="stMarkdownContainer"] h1, [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3, [data-testid="stMarkdownContainer"] h4,
    [data-testid="stMarkdownContainer"] h5 {{
        font-family: {FONT_DISPLAY} !important;
        font-weight: 800 !important;
        letter-spacing: .01em;
        color: {INK};
    }}
    [data-testid="stHeading"] h3, [data-testid="stMarkdownContainer"] h3 {{
        font-size: 1.9rem !important;
    }}
    [data-testid="stMarkdownContainer"] h5 {{
        font-size: 1.35rem !important;
    }}
    /* h6 (######) is used across the app for sub-section labels ("This
       week's biggest edges", "Closing Line Value", "Hit rate by week") -
       same display face, one step smaller than h5. */
    h6, [data-testid="stMarkdownContainer"] h6 {{
        font-family: {FONT_DISPLAY} !important;
        font-weight: 700 !important;
        font-size: 1.2rem !important;
        letter-spacing: .02em;
        color: {INK};
    }}
    [data-testid="stMarkdownContainer"] h6 span {{
        font-size: inherit !important;
        font-family: inherit !important;
    }}
    code, pre, [data-testid="stCode"] {{
        font-family: {FONT_MONO} !important;
    }}

    /* ---- This app's own component classes ----------------------------- */
    .player-card {{
        background: {SURFACE};
        border: 1px solid {LINE};
        border-radius: 6px;
        padding: 18px 20px;
        margin-bottom: 14px;
    }}
    .player-card img {{ border-radius: 50%; object-fit: cover; }}
    .stat-big {{ font-family: {FONT_DISPLAY}; font-size: 38px; font-weight: 800; margin-top: 8px;
                 color: {INK}; line-height: 1.05; font-variant-numeric: tabular-nums; }}
    .stat-label {{ font-size: 13px; color: {SUB}; letter-spacing: .06em; text-transform: uppercase;
                   font-weight: 600; }}
    .delta-up {{ color: {GOOD}; font-weight: 700; font-size: 15px; }}
    .delta-down {{ color: {BAD}; font-weight: 700; font-size: 15px; }}
    .delta-flat {{ color: {SUB}; font-weight: 600; font-size: 15px; }}
    .consistency-badge {{
        display: inline-block; font-size: 13px; padding: 4px 11px;
        border-radius: 4px; margin-top: 6px; margin-right: 4px;
        background: {SURFACE_2}; color: {SUB}; border: 1px solid {LINE};
    }}
    .matchup-badge {{
        display: inline-block; font-size: 13px; padding: 4px 11px;
        border-radius: 4px; margin-top: 6px; margin-right: 4px;
        background: {SURFACE_2}; border: 1px solid {LINE};
        font-family: {FONT_MONO}; letter-spacing: .01em;
        /* text color is set inline per-badge, gradient by matchup difficulty */
    }}
    .injury-badge {{
        display: inline-block; font-size: 13px; padding: 4px 11px;
        border-radius: 4px; margin-top: 6px; margin-right: 4px;
        background: {BAD_SOFT}; color: {BAD}; border: 1px solid {BAD_BORDER};
    }}
    /* Hot Picks' continuous-improvement loop: how confident to be in a
       (category, position) segment based on its Track Record hit rate. */
    .confidence-badge {{
        display: inline-block; font-size: 13px; padding: 4px 11px;
        border-radius: 4px; margin-top: 6px; margin-right: 4px;
    }}
    .confidence-high {{
        background: {GOOD_SOFT}; color: {GOOD}; border: 1px solid {GOOD_BORDER};
    }}
    .confidence-neutral {{
        background: {SURFACE_2}; color: {SUB}; border: 1px solid {LINE};
    }}
    .confidence-low {{
        background: {BAD_SOFT}; color: {BAD}; border: 1px solid {BAD_BORDER};
    }}
    .confidence-new {{
        background: {SURFACE_2}; color: {SUB}; border: 1px dashed {LINE};
    }}
    /* Track Record result badges - one status color per outcome. */
    .result-badge {{
        display: inline-block; font-size: 13px; padding: 4px 11px;
        border-radius: 4px; margin-top: 6px; margin-right: 4px; font-weight: 700;
    }}
    .result-hit {{
        background: {GOOD_SOFT}; color: {GOOD}; border: 1px solid {GOOD_BORDER};
    }}
    .result-miss {{
        background: {BAD_SOFT}; color: {BAD}; border: 1px solid {BAD_BORDER};
    }}
    .result-push {{
        background: {SURFACE_2}; color: {SUB}; border: 1px solid {LINE};
    }}
    .result-pending {{
        background: {WARN_SOFT}; color: {WARN}; border: 1px solid {WARN_BORDER};
    }}
    /* Initials avatar shown in place of a headshot when a player has no
       photo on file - keeps every card the same size/shape. */
    .hotpick-avatar-fallback {{
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        flex-shrink: 0;
        font-variant-numeric: tabular-nums;
    }}

    /* ---- Dashboard alerts: compact single-line notices ------------------- */
    .dash-alert {{
        display: flex; align-items: center; gap: 10px;
        padding: 8px 14px; margin-bottom: 6px; border-radius: 4px;
    }}
    [data-testid="stMarkdownContainer"] .dash-alert-icon {{ font-size: 16px !important; flex-shrink: 0; }}
    [data-testid="stMarkdownContainer"] .dash-alert-text {{ font-size: 14px !important; color: {INK}; }}

    /* ---- Roster alerts strip (Lineups page) ------------------------------ */
    [data-testid="stMarkdownContainer"] .ra-roster {{
        font-family: {FONT_MONO}; font-size: 12px !important; font-weight: 600;
        letter-spacing: .08em; text-transform: uppercase; color: {ACCENT};
        margin: 12px 0 4px;
    }}
    [data-testid="stMarkdownContainer"] .ra-roster:first-child {{ margin-top: 0; }}
    .ra-row {{
        display: flex; align-items: center; gap: 10px;
        padding: 5px 0; border-bottom: 1px solid {LINE};
    }}
    [data-testid="stMarkdownContainer"] .ra-pill {{
        font-family: {FONT_MONO}; font-size: 11px !important; font-weight: 700;
        padding: 2px 0; width: 104px; text-align: center; border-radius: 3px; flex-shrink: 0;
    }}
    .ra-bad {{ background: {BAD_SOFT}; color: {BAD}; border: 1px solid {BAD_BORDER}; }}
    .ra-warn {{ background: {WARN_SOFT}; color: {WARN}; border: 1px solid {WARN_BORDER}; }}
    .ra-bye {{ background: {SURFACE_2}; color: {SUB}; border: 1px solid {LINE}; }}
    [data-testid="stMarkdownContainer"] .ra-name {{ font-size: 14px !important; font-weight: 700; color: {INK}; }}
    [data-testid="stMarkdownContainer"] .ra-sub {{ font-size: 13px !important; color: {SUB}; }}

    /* ---- Header / wordmark ----------------------------------------------
       Typographic wordmark replacing the old raster logo image: set in the
       display face, so it's crisp at any size and needs no image asset. */
    .tps-header {{
        display: flex; align-items: flex-end; justify-content: space-between;
        flex-wrap: wrap; gap: 8px 24px;
        padding: 18px 0 12px;
    }}
    .tps-wordmark {{
        font-family: {FONT_DISPLAY};
        font-weight: 900;
        font-size: clamp(34px, 4.2vw, 54px);
        line-height: .92;
        letter-spacing: .01em;
        color: {INK};
        display: flex; align-items: center; gap: 14px;
        text-transform: uppercase;
    }}
    /* The global "[data-testid=stMarkdownContainer] span {{ font-size: 1rem }}"
       rule above would otherwise shrink the wordmark's inner spans back to
       body size - this out-specifies it so they inherit the display size. */
    [data-testid="stMarkdownContainer"] .tps-wordmark span {{
        font-size: inherit !important;
        font-family: inherit !important;
    }}
    .tps-wordmark .tps-dot {{
        width: 13px; height: 13px; border-radius: 50%;
        background: {ACCENT}; flex: none;
    }}
    .tps-wordmark .tps-shop {{ color: {ACCENT}; }}
    .tps-tagline {{
        font-family: {FONT_MONO};
        font-size: 13px; font-weight: 500;
        color: {SUB}; letter-spacing: .14em; text-transform: uppercase;
        padding-bottom: 6px;
    }}
    .tps-divider {{
        height: 2px; margin: 0 0 18px;
        background: linear-gradient(90deg, {ACCENT} 0%, rgba(212,169,52,.35) 35%, {LINE} 70%);
    }}

    /* ---- Buttons --------------------------------------------------------- */
    .stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
        border-radius: 4px;
        border: 1px solid {LINE};
        font-weight: 700;
        font-size: 15px;
        padding: 10px 18px;
        transition: border-color .15s ease, color .15s ease;
    }}
    .stButton > button:hover, .stFormSubmitButton > button:hover {{
        border-color: {ACCENT};
        color: {ACCENT};
    }}
    /* Form submit buttons (st.form_submit_button) are tagged
       kind="primaryFormSubmit" / "secondaryFormSubmit", not "primary" -
       without these selectors "Save slip" / "Save roster" fell back to
       Streamlit's own primaryColor instead of the brand gold. */
    button[kind="primary"], button[kind="primaryFormSubmit"],
    .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"],
    .stFormSubmitButton > button[kind="primaryFormSubmit"] {{
        background: {ACCENT} !important;
        color: {ACCENT_INK} !important;
        border: none !important;
    }}
    button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover {{
        background: {ACCENT_HOVER} !important;
        color: {ACCENT_INK} !important;
    }}
    button[kind="primary"] p, button[kind="primaryFormSubmit"] p {{
        color: {ACCENT_INK} !important;
        font-weight: 700 !important;
    }}
    button[kind="secondaryFormSubmit"] {{
        border-radius: 4px; border: 1px solid {LINE}; font-weight: 700;
    }}
    button[kind="secondaryFormSubmit"]:hover {{
        border-color: {ACCENT}; color: {ACCENT};
    }}

    /* ---- Tabs ------------------------------------------------------------ */
    [data-testid="stTabs"] [role="tablist"] {{
        display: flex;
        width: 100%;
        justify-content: space-between;
        gap: 4px;
        border-bottom: 1px solid {LINE};
    }}
    [data-testid="stTabs"] button[role="tab"] {{
        flex: 1 1 0;
        justify-content: center;
        font-weight: 700; color: {SUB};
        padding: 16px 16px;
    }}
    [data-testid="stTabs"] button[role="tab"] p {{
        font-size: 17px !important;
        font-weight: 700 !important;
    }}
    [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {{
        color: {INK};
    }}
    [data-testid="stTabs"] [data-baseweb="tab-highlight"] {{
        background-color: {ACCENT} !important;
    }}
    @media (max-width: 900px) {{
        [data-testid="stTabs"] [role="tablist"] {{
            overflow-x: auto;
        }}
        [data-testid="stTabs"] button[role="tab"] {{
            flex: 0 0 auto;
        }}
    }}

    /* ---- Site-section nav (the top-level radio) -------------------------
       Styled as an underline nav bar rather than a pill toggle: uppercase,
       tracked labels, a gold underline on the active section. */
    /* Streamlit shrink-wraps the radio's containers to their content, so
       the nav's bottom rule would stop short - stretch them full width. */
    [data-testid="stElementContainer"]:has(> div[data-testid="stRadio"]),
    div[data-testid="stRadio"] {{
        width: 100% !important;
    }}
    div[data-testid="stRadio"] > div[role="radiogroup"] {{
        gap: 6px 26px; background: transparent; border: none;
        border-bottom: 1px solid {LINE};
        border-radius: 0; padding: 0; display: flex; flex-wrap: wrap; width: 100%;
        margin-bottom: 26px;
    }}
    div[data-testid="stRadio"] label {{
        border-radius: 0 !important; padding: 10px 2px 11px !important; margin: 0 0 -1px 0 !important;
        border-bottom: 3px solid transparent; transition: border-color .15s ease;
    }}
    /* Hide the radio circle - the gold underline is the indicator. Two
       selectors because Streamlit's radio DOM differs by version: newer
       builds render label[data-testid=stRadioOption] > span(input) + div >
       [div(circle), div(markdown text)]; older baseweb builds render the
       circle as the label's first child div. */
    [data-testid="stRadioOption"] > div > div:not([data-testid="stMarkdownContainer"]),
    div[data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child {{
        display: none !important;
    }}
    div[data-testid="stRadio"] label p {{
        font-size: 15px !important; font-weight: 700 !important;
        letter-spacing: .06em; text-transform: uppercase; color: {SUB};
    }}
    div[data-testid="stRadio"] label:hover p {{
        color: {INK};
    }}
    div[data-testid="stRadio"] label:has(input:checked),
    [data-testid="stRadioOption"][data-selected="true"] {{
        border-bottom-color: {ACCENT};
    }}
    div[data-testid="stRadio"] label:has(input:checked) p,
    [data-testid="stRadioOption"][data-selected="true"] p {{
        color: {INK} !important;
    }}

    /* ---- Metrics --------------------------------------------------------- */
    [data-testid="stMetric"] {{
        background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px;
        padding: 16px 18px;
    }}
    [data-testid="stMetricValue"] {{
        color: {ACCENT}; font-variant-numeric: tabular-nums;
        font-family: {FONT_DISPLAY} !important; font-weight: 800;
        font-size: 2.6rem !important;
    }}
    [data-testid="stMetricLabel"] p {{
        font-size: 14px !important; font-weight: 700 !important;
        letter-spacing: .05em; text-transform: uppercase; color: {SUB};
    }}

    /* ---- Dataframes / tables --------------------------------------------- */
    [data-testid="stDataFrame"] {{
        border: 1px solid {LINE}; border-radius: 6px; overflow: hidden;
    }}

    /* ---- Expanders ------------------------------------------------------- */
    [data-testid="stExpander"] {{
        background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px;
    }}

    /* ---- Popovers (ℹ️ details buttons) ------------------------------------
       These are reference/help buttons, not actions - styled small and
       quiet (compact, muted text, hairline border) so they sit out of the
       way instead of reading like primary controls. */
    [data-testid="stPopoverButton"], [data-testid="stPopover"] > div > button {{
        border-radius: 4px !important;
        padding: 3px 10px !important;
        min-height: 0 !important;
        background: transparent !important;
        border: 1px solid {LINE} !important;
        color: {SUB} !important;
    }}
    [data-testid="stPopoverButton"] p, [data-testid="stPopover"] > div > button p {{
        font-size: 13px !important;
        font-weight: 600 !important;
        color: {SUB} !important;
    }}
    [data-testid="stPopoverButton"]:hover, [data-testid="stPopover"] > div > button:hover {{
        border-color: {ACCENT} !important;
    }}
    [data-testid="stPopoverButton"]:hover p, [data-testid="stPopover"] > div > button:hover p {{
        color: {ACCENT} !important;
    }}
    /* Popover panel itself: roomy enough for the longer explanations. */
    [data-testid="stPopoverBody"] {{
        max-width: 620px;
    }}

    /* ---- Forms / inputs --------------------------------------------------- */
    [data-testid="stForm"] {{
        background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px; padding: 18px;
    }}
    .stTextInput input, .stNumberInput input, [data-baseweb="select"] > div {{
        background: {SURFACE_2} !important; border-color: {LINE} !important;
    }}

    /* ---- Alerts (info/warning/success/error) ------------------------------ */
    [data-testid="stAlertContainer"] {{
        border-radius: 6px; border-width: 1px; border-style: solid;
    }}

    /* ---- Sidebar ---------------------------------------------------------- */
    [data-testid="stSidebar"] {{
        background: {SURFACE}; border-right: 1px solid {LINE};
    }}

    /* ---- Multiselect/selectbox selected-value tags -------------------------
       Tinted chip rather than a solid fill, so the gold stays reserved for
       primary actions and active states. */
    [data-testid="stMultiSelectTagsContainer"] [data-tag] {{
        background: {ACCENT_SOFT} !important;
        border: 1px solid {ACCENT_SOFT_STRONG} !important;
        border-radius: 4px !important;
    }}
    [data-testid="stMultiSelectTagsContainer"] [data-tag] span {{
        color: {ACCENT} !important;
        font-size: 14px !important;
        font-weight: 600;
    }}

    /* ---- Dividers --------------------------------------------------------- */
    hr {{ border-color: {LINE} !important; }}

    /* ---- Captions --------------------------------------------------------- */
    [data-testid="stCaptionContainer"] p {{
        font-size: 14px !important;
        color: {SUB};
    }}
    </style>
    """, unsafe_allow_html=True)


def info_popover(text: str, label: str = "ℹ️ Details", *, use_container_width: bool = False) -> None:
    """A small icon/label button that reveals an explanatory paragraph on
    click, instead of that paragraph sitting on the page permanently as a
    st.caption(). Used for the longer methodology/legend write-ups (how
    projections are calculated, badge-key legends, data-source notes) that
    were bulking up every tab - the short one-line status captions (e.g.
    'props pulled Xs ago') are left as plain captions since those are quick
    orientation info, not the kind of thing worth an extra click to see."""
    with st.popover(label, use_container_width=use_container_width):
        st.markdown(text)


def render_header() -> None:
    """Typographic wordmark + tagline masthead, shown once at the very top
    of the page, above the site-section nav. Replaces the original raster
    logo image (lime-green "SHOP" lettering) - pure HTML/CSS in the brand
    display face, so there's no image asset to load or keep in sync."""
    st.markdown(
        """
        <div class="tps-header">
            <div class="tps-wordmark"><span class="tps-dot"></span><span>The Prop <span class="tps-shop">Shop</span></span></div>
            <div class="tps-tagline">Fantasy football &amp; prop analytics</div>
        </div>
        <div class="tps-divider"></div>
        """,
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    """Small credit line at the very bottom of the page - shown once,
    after everything else has rendered, so it appears on every section and
    sub-tab. Styled as a quiet, muted caption - a signature, not a banner."""
    st.markdown(
        f"""
        <div class="tps-divider" style="margin-top:32px;"></div>
        <div style="text-align:center; color:{SUB}; font-size:13px; padding:10px 0 18px; letter-spacing:.04em;">
            Presented to you by: Curtis J Elias
        </div>
        """,
        unsafe_allow_html=True,
    )
