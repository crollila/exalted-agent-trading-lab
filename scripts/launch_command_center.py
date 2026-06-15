from __future__ import annotations

import argparse
from pathlib import Path

from src.ui.launcher import launch_command_center


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the ExaltedFable Command Center.")
    parser.add_argument("--port", type=int, help="Optional Streamlit port.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser tab before starting Streamlit.",
    )
    args = parser.parse_args()
    raise SystemExit(
        launch_command_center(
            dashboard_path=Path("src/ui/dashboard.py"),
            port=args.port,
            open_browser=not args.no_browser,
        )
    )


if __name__ == "__main__":
    main()
