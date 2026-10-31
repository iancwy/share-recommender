"""Design tokens: the one place for colours, fonts, spacing and chart palettes.

These hold the current look (Streamlit's defaults plus the dataviz reference palette).
TODO: from Ian's design. When his Claude Design export lands in docs/design/, map it here
and in .streamlit/config.toml [theme]; nothing else should hard-code a colour.
"""

# Streamlit theme values, kept in step with .streamlit/config.toml.
PRIMARY = "#ff4b4b"           # TODO: from Ian's design
FONT = "sans serif"           # TODO: from Ian's design
RADIUS = "0.5rem"             # TODO: from Ian's design
SPACING = "1rem"              # TODO: from Ian's design

# Chart colours, light and dark steps (dataviz reference palette). TODO: from Ian's design.
PALETTE = {
    "light": {"series": ["#2a78d6", "#eb6834"], "muted": "#898781", "grid": "#e1e0d9"},
    "dark": {"series": ["#3987e5", "#d95926"], "muted": "#898781", "grid": "#2c2c2a"},
}

# Health check severities: always an icon and a label, never colour alone.
SEVERITY_BADGE = {
    "alert": "⛔ Alert",
    "warning": "⚠️ Warning",
    "info": "ℹ️ Info",
    "ok": "✅ OK",
}
