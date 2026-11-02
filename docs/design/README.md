# Design

Put the Claude Design export here (screenshots, HTML/CSS, or the colour and font list).
Claude Code reads this folder to implement the redesign.

When it's here, the design is applied in this order (see the Week 4 brief, item 6):

1. `.streamlit/config.toml` `[theme]`: everything the installed Streamlit version supports.
2. `share_recommender/ui/theme.py`: design tokens, including chart colours (light and dark).
3. Only then a small amount of CSS, injected from `theme.py`, for what the theme can't do.

No colours or fonts are hard-coded anywhere else.
