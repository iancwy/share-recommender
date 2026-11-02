"""Entry point for Streamlit Community Cloud."""
import runpy

runpy.run_module("share_recommender.ui.app", run_name="__main__")
