# Film Analysis Skill

This skill implements the Cine pipeline C0+C1 phases:

- **C0**: Project creation, filelock-based single-writer mutex, atomic writes, revision management
- **C1**: Scene cut detection, evidence extraction, validation, HTML report rendering

## Usage

```python
from pipeline import run_pipeline
from pathlib import Path

result = run_pipeline(
    media_path=Path("video.mp4"),
    projects_dir=Path("scratch/projects"),
    display_name="My Film",
    scope_in_seconds=0.0,
    scope_out_seconds=90.0,
)
print(result)
```

## Design principles

- Source PTS (integer) + time_base (Fraction) are authoritative
- VFR videos never use avg_frame_rate for cut PTS
- ffprobe/ffmpeg calls use argv lists — no shell string concat
- Missing deps → `unavailable` status, no fabricated data
- Atomic writes: tempfile same FS + os.replace
- Intervals are half-open [in_pts, out_pts)
- Non-zero start_time preserved; source PTS ≠ playback time
