# Design

The design is the "Shares" style guide and screens on Claude Design (private to Ian):
https://claude.ai/artifact/5bRu6VW96ogASXoFSxoubg. It has colour tokens for light and
dark, fonts, the chart palette, status badges, the app icon, and phone and desktop
mockups of every page. Its tokens are applied in `.streamlit/config.toml` and
`share_recommender/ui/theme.py`.

When it's here, the design is applied in this order (see the Week 4 brief, item 6):

1. `.streamlit/config.toml` `[theme]`: everything the installed Streamlit version supports.
2. `share_recommender/ui/theme.py`: design tokens, including chart colours (light and dark).
3. Only then a small amount of CSS, injected from `theme.py`, for what the theme can't do.

No colours or fonts are hard-coded anywhere else.
