"""
theme.py
The Prop Shop's visual identity - brand colors, the global CSS that skins
Streamlit's native widgets to match, and the logo header shown on every
page. One module so every part of the app pulls from the same palette
instead of each feature inventing its own colors (the standing rule this
codebase already follows for behavior applies to styling too).

Colors are sampled directly from the actual logo file (assets/logo.webp)
rather than eyeballed, so the header and the rest of the UI are a genuine
color match, not an approximation:
    green (SHOP lettering)  -> (104, 246, 31) -> #68F61F
Everything else in the palette (surfaces, borders, muted text) is built
around that green and the logo's black/white/chalk-gray palette - kept
deliberately narrow (black, white, green, gray, plus red/amber ONLY for
semantic meaning) rather than introducing unrelated colors.
"""

import base64
from pathlib import Path

import streamlit as st

LOGO_PATH = Path(__file__).parent / "assets" / "logo.webp"

# ---- Brand palette -----------------------------------------------------
# Core
BG = "#0A0C0A"              # app background - near-black, faint green undertone
SURFACE = "#14170F"          # cards, sidebar, secondary background
SURFACE_2 = "#1C211A"        # nested/inset surfaces (inputs, code-style chips)
LINE = "#2B2F26"             # borders/dividers
INK = "#F4F5F1"              # primary text - warm off-white
SUB = "#9AA092"              # secondary/muted text - green-gray

# Brand accent (sampled from the logo)
ACCENT = "#68F61F"
ACCENT_HOVER = "#4AB116"
ACCENT_SOFT = "rgba(104, 246, 31, 0.14)"   # badge/chip fills
ACCENT_SOFT_STRONG = "rgba(104, 246, 31, 0.24)"

# Semantic (separate from the brand accent - meaning, not decoration)
BAD = "#E5484D"
BAD_SOFT = "rgba(229, 72, 77, 0.14)"
WARN = "#F5B324"
WARN_SOFT = "rgba(245, 179, 36, 0.14)"

# Matchup-difficulty gradient endpoints (toughest -> easiest), used by
# matchup_rank_color() in dashboard.py - easiest end is brand-green-tinted
# rather than a generic pastel green, so that scale reads as "on brand"
# even though it's a semantic (not decorative) use of color.
MATCHUP_TOUGH_RGB = (255, 107, 107)
MATCHUP_EASY_RGB = (142, 248, 87)


@st.cache_data
def _logo_data_uri() -> str:
    """Base64-embeds the logo so it renders reliably via st.markdown HTML
    regardless of Streamlit Cloud's static file serving - same approach
    already used throughout this app for badges/cards. Cached so the file
    is only read+encoded once per session, not on every rerun."""
    data = LOGO_PATH.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/webp;base64,{encoded}"


def inject_css() -> None:
    """Global CSS: recolors this app's own custom classes (player-card,
    badges, deltas - defined here so dashboard.py's inline style block can
    be dropped in favor of this single source of truth) AND overrides
    Streamlit's native widgets (buttons, tabs, metrics, dataframes, forms,
    expanders, alerts, the Fantasy Lineups/Prop Bets radio) via their
    data-testid attributes, which are far more stable across Streamlit
    versions than its internal CSS class names. Call once, right after
    st.set_page_config()."""
    st.markdown(f"""
    <style>
    /* ---- Base type & background -------------------------------------- */
    html, body, [class*="css"] {{
        font-family: "IBM Plex Sans", "Source Sans Pro", sans-serif;
    }}
    .stApp {{
        background: {BG};
    }}

    /* ---- Global type scale -------------------------------------------
       This app is used on laptop/desktop monitors, not phones, so text
       leans larger than Streamlit's own (phone-friendly) defaults across
       the board - body copy, labels, buttons - rather than the other way
       around. Tabs and headshots/logos get their own, bigger bump below. */
    html, body {{ font-size: 17px; }}
    [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li,
    [data-testid="stMarkdownContainer"] span {{
        font-size: 1rem;
    }}
    [data-testid="stWidgetLabel"] p, label {{
        font-size: 1rem !important;
    }}
    .stSelectbox div, .stMultiSelect div, .stTextInput input, .stNumberInput input {{
        font-size: 0.95rem;
    }}

    /* ---- This app's own component classes ----------------------------- */
    .player-card {{
        background: {SURFACE};
        border: 1px solid {LINE};
        border-radius: 12px;
        padding: 18px 20px;
        margin-bottom: 14px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.35);
    }}
    .player-card img {{ border-radius: 50%; object-fit: cover; }}
    .stat-big {{ font-size: 32px; font-weight: 700; margin-top: 8px; color: {INK};
                 font-variant-numeric: tabular-nums; }}
    .stat-label {{ font-size: 13px; color: {SUB}; letter-spacing: .02em; text-transform: uppercase; }}
    .delta-up {{ color: {ACCENT}; font-weight: 600; font-size: 15px; }}
    .delta-down {{ color: {BAD}; font-weight: 600; font-size: 15px; }}
    .delta-flat {{ color: {SUB}; font-weight: 600; font-size: 15px; }}
    .consistency-badge {{
        display: inline-block; font-size: 13px; padding: 4px 11px;
        border-radius: 10px; margin-top: 6px; margin-right: 4px;
        background: {SURFACE_2}; color: {SUB}; border: 1px solid {LINE};
    }}
    .matchup-badge {{
        display: inline-block; font-size: 13px; padding: 4px 11px;
        border-radius: 10px; margin-top: 6px; margin-right: 4px;
        background: {SURFACE_2}; border: 1px solid {LINE};
        font-family: "IBM Plex Mono", monospace; letter-spacing: .01em;
        /* text color is set inline per-badge, gradient by matchup difficulty */
    }}
    .injury-badge {{
        display: inline-block; font-size: 13px; padding: 4px 11px;
        border-radius: 10px; margin-top: 6px; margin-right: 4px;
        background: {BAD_SOFT}; color: {BAD}; border: 1px solid rgba(229,72,77,.3);
    }}

    /* ---- Header / masthead --------------------------------------------- */
    /* Logo is a transparent-background wordmark, stretched to span almost
       the full page width as the main hero banner - sized by width (not a
       fixed height) so it scales with the page and stays this dominant on
       any desktop monitor, capped so it doesn't get absurd on ultrawides. */
    .tps-header {{
        padding: 14px 0 8px;
        margin-bottom: 4px;
        display: flex;
        justify-content: center;
    }}
    .tps-header img {{
        display: block;
        width: 94%;
        max-width: 1700px;
        height: auto;
        object-fit: contain;
    }}
    @media (max-width: 640px) {{
        .tps-header img {{ width: 98%; }}
    }}
    .tps-tagline {{ color: {SUB}; font-size: 13px; margin: -2px 0 14px 2px; letter-spacing: .01em; }}
    .tps-divider {{
        height: 2px; border-radius: 2px; margin: 0 0 18px;
        background: linear-gradient(90deg, {ACCENT} 0%, rgba(104,246,31,0) 70%);
    }}

    /* ---- Buttons --------------------------------------------------------- */
    .stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
        border-radius: 8px;
        border: 1px solid {LINE};
        font-weight: 600;
        font-size: 15px;
        padding: 10px 18px;
        transition: border-color .15s ease, transform .05s ease;
    }}
    .stButton > button:hover, .stFormSubmitButton > button:hover {{
        border-color: {ACCENT};
        color: {ACCENT};
    }}
    button[kind="primary"], .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
        background: {ACCENT} !important;
        color: #06210A !important;
        border: none !important;
    }}
    button[kind="primary"]:hover {{
        background: {ACCENT_HOVER} !important;
        color: #06210A !important;
    }}

    /* ---- Tabs --------------------------------------------------------------
       Default Streamlit packs tabs tightly against the left edge. Spreading
       them across the full width (space-between the tab list, flex-grow on
       each tab) makes the tab bar read as a proper section-navigator rather
       than a cramped row of labels, especially with 6-8 tabs on a wide page. */
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
        font-weight: 600; color: {SUB};
        padding: 16px 16px;
    }}
    [data-testid="stTabs"] button[role="tab"] p {{
        font-size: 18px !important;
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

    /* ---- Fantasy Lineups / Prop Bets radio -> pill toggle ------------------ */
    div[data-testid="stRadio"] > div[role="radiogroup"] {{
        gap: 4px; background: {SURFACE}; border: 1px solid {LINE};
        border-radius: 999px; padding: 4px; display: inline-flex; width: fit-content;
    }}
    div[data-testid="stRadio"] label {{
        border-radius: 999px !important; padding: 9px 20px !important; margin: 0 !important;
        transition: background .15s ease;
    }}
    div[data-testid="stRadio"] label p {{
        font-size: 16px !important;
    }}
    div[data-testid="stRadio"] label:has(input:checked) {{
        background: {ACCENT};
    }}
    div[data-testid="stRadio"] label:has(input:checked) p {{
        color: #06210A !important; font-weight: 700 !important;
    }}

    /* ---- Metrics --------------------------------------------------------- */
    [data-testid="stMetric"] {{
        background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px;
        padding: 16px 18px;
    }}
    [data-testid="stMetricValue"] {{
        color: {ACCENT}; font-variant-numeric: tabular-nums;
        font-size: 2.3rem !important;
    }}
    [data-testid="stMetricLabel"] p {{
        font-size: 15px !important;
    }}

    /* ---- Dataframes / tables ----------------------------------------------- */
    [data-testid="stDataFrame"] {{
        border: 1px solid {LINE}; border-radius: 10px; overflow: hidden;
    }}

    /* ---- Expanders --------------------------------------------------------- */
    [data-testid="stExpander"] {{
        background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px;
    }}

    /* ---- Forms / inputs ----------------------------------------------------- */
    [data-testid="stForm"] {{
        background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px; padding: 18px;
    }}
    .stTextInput input, .stNumberInput input, [data-baseweb="select"] > div {{
        background: {SURFACE_2} !important; border-color: {LINE} !important;
    }}

    /* ---- Alerts (info/warning/success/error) -------------------------------- */
    [data-testid="stAlertContainer"] {{
        border-radius: 10px; border-width: 1px; border-style: solid;
    }}

    /* ---- Sidebar ------------------------------------------------------------ */
    [data-testid="stSidebar"] {{
        background: {SURFACE}; border-right: 1px solid {LINE};
    }}

    /* ---- Multiselect/selectbox selected-value tags --------------------------
       Streamlit's theme engine fills these solid with primaryColor by
       default, which reads as too loud repeated 4-5x in the Position
       filter - softened to a tinted chip so the vivid green stays reserved
       for primary actions and active states (the pill toggle, active tab,
       primary buttons), not scattered across every filter tag. */
    [data-testid="stMultiSelectTagsContainer"] [data-tag] {{
        background: {ACCENT_SOFT_STRONG} !important;
        border: 1px solid rgba(104, 246, 31, 0.35) !important;
    }}
    [data-testid="stMultiSelectTagsContainer"] [data-tag] span {{
        color: {ACCENT} !important;
        font-size: 14px !important;
    }}

    /* ---- Captions -------------------------------------------------------- */
    [data-testid="stCaptionContainer"] p {{
        font-size: 14px !important;
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
    """Logo + tagline masthead, shown once at the very top of the page -
    above the Fantasy Lineups / Prop Bets split, so it's visible on both
    sides of the site rather than duplicated per-side."""
    st.markdown(
        f"""
        <div class="tps-header">
            <img src="{_logo_data_uri()}" alt="The Prop Shop" />
        </div>
        <div class="tps-divider"></div>
        """,
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    """Small credit line at the very bottom of the page - shown once,
    after everything else has rendered, so it appears on every tab
    regardless of which side (Fantasy Lineups / Prop Bets) or sub-tab is
    active. Styled as a quiet, muted caption (same SUB color as the rest
    of the app's secondary text) rather than a bold callout - a signature,
    not another banner."""
    st.markdown(
        f"""
        <div class="tps-divider" style="margin-top:32px;"></div>
        <div style="text-align:center; color:{SUB}; font-size:12px; padding:10px 0 18px;">
            Presented to you by: Curtis J Elias
        </div>
        """,
        unsafe_allow_html=True,
    )
