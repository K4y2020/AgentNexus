"""Compatibility entry point for the canonical C0+C1 pipeline."""

from pathlib import Path

from pipeline.entry import index_media, main


def initialize(video: Path, output: Path) -> dict:
    return index_media(video, output)


if __name__ == "__main__":
    main()
