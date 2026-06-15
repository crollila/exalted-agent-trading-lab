"""Local-only operator dashboard package for the ExaltedFable Agent Trading Lab.

Pure, testable helpers live in :mod:`src.ui.dashboard_state`. The Streamlit entrypoint
lives in :mod:`src.ui.dashboard` and only renders those helpers, so the operator console
never duplicates trading logic or bypasses existing safety gates.
"""
