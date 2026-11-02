"""Design tokens: the one place for colours, fonts, spacing and chart palettes.

From Ian's design ("Shares" style guide, docs/design/README.md): a calm family-finance
notebook, teal on warm off-white, Plus Jakarta Sans headings over Inter. Theme colours and
fonts live in .streamlit/config.toml; this file holds what the app needs in Python, plus
the little CSS the theme can't do. Nothing else should hard-code a colour.
"""
from pathlib import Path

# Streamlit theme values, kept in step with .streamlit/config.toml.
PRIMARY = {"light": "#1F6F78", "dark": "#5DB8B3"}
FONT = "Inter"
HEADING_FONT = "Plus Jakarta Sans"
RADIUS = "10px"
SPACING = "1rem"

# App icon: three rising bars on teal. Used for the browser tab and the nav bar logo.
ICON = str(Path(__file__).with_name("icon.svg"))

# Chart colours, light and dark steps. "series" is Value then Money invested; the
# categorical list matches chartCategoricalColors in config.toml.
PALETTE = {
    "light": {"series": ["#1F6F78", "#8A99A3"], "muted": "#66737A", "grid": "#E7E3DA",
              "positive": "#1A7A4C", "negative": "#B4432A",
              "categorical": ["#1F6F78", "#D08A2E", "#5B6CB8", "#7A9E4F", "#A2557F",
                              "#8A99A3"]},
    "dark": {"series": ["#5DB8B3", "#A7B4BB"], "muted": "#92A1A6", "grid": "#253034",
             "positive": "#5DCB92", "negative": "#F28C70",
             "categorical": ["#5DB8B3", "#E9A852", "#909EE8", "#A6C879", "#D78BB5",
                             "#A7B4BB"]},
}

# Health check severities: always an icon and a label, never colour alone.
SEVERITY_BADGE = {
    "alert": ":red[:material/report:] Alert",
    "warning": ":orange[:material/warning:] Warning",
    "info": ":blue[:material/info:] Info",
    "ok": ":green[:material/check_circle:] OK",
}

# What config.toml can't do: big numbers in the heading font with equal-width digits, and
# 44px tap targets for buttons and tabs on a phone.
CSS = f"""<style>
[data-testid="stMetricValue"] {{
  font-family: "{HEADING_FONT}", "{FONT}", sans-serif;
  font-variant-numeric: tabular-nums;
}}
[data-testid="stDataFrame"], [data-testid="stTable"] {{ font-variant-numeric: tabular-nums; }}
[data-testid^="stBaseButton"], [data-testid="stTab"] {{ min-height: 44px; }}
/* Streamlit puts white text on primary buttons, too faint on the light dark-mode teal. */
@media (prefers-color-scheme: dark) {{
  [data-testid^="stBaseButton-primary"], [data-testid^="stBaseButton-primary"] * {{
    color: #0D1B1C !important;
  }}
}}
</style>"""
