"""Compatibility entry point for the canonical C0+C1 pipeline.

@deprecated since 0.12.0; removed in 0.13.0. Use the cine-film-analysis MCP
tools instead: film_analyze to index and film_refresh_report to re-render.
"""

import sys
from pathlib import Path

from pipeline.entry import index_media, main

DEPRECATION = (
    "media_project.py is deprecated and will be removed in 0.13.0; use the "
    "cine-film-analysis MCP tools film_analyze and film_refresh_report."
)


def initialize(video: Path, output: Path) -> dict:
    return index_media(video, output)


if __name__ == "__main__":
    # stdout carries the JSON receipt, so the notice goes to stderr.
    print(DEPRECATION, file=sys.stderr)
    main()
