"""Cine pipeline Pydantic v2 schemas — C0+C1."""
from __future__ import annotations

import uuid
from enum import Enum
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.0.0"


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    waiting_input = "waiting_input"
    interrupted = "interrupted"
    failed = "failed"
    finished = "finished"


class VerificationStatus(str, Enum):
    unreviewed = "unreviewed"
    model_reviewed = "model_reviewed"
    human_reviewed = "human_reviewed"
    disputed = "disputed"
    unavailable = "unavailable"


class CandidateStatus(str, Enum):
    candidate = "candidate"
    accepted = "accepted"
    rejected = "rejected"


class DetectorStatus(str, Enum):
    ok = "ok"
    unavailable = "unavailable"


# ---------------------------------------------------------------------------
# Base model — all cine records carry these fields (B1)
# ---------------------------------------------------------------------------

class CineBaseRecord(BaseModel):
    """
    All Cine records inherit these fields.
    Bootstrap records (ProjectRecord, SourceMediaRecord at creation time before
    a revision exists) may have revision_id=None.
    """
    schema_version: str = SCHEMA_VERSION
    source_id: Optional[str] = None
    revision_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Time interval — half-open [in_pts, out_pts) in source PTS units
# ---------------------------------------------------------------------------

class PtsInterval(BaseModel):
    """Half-open [in_pts, out_pts) interval in source integer PTS."""
    in_pts: int
    out_pts: int
    time_base_num: int = 1
    time_base_den: int = 1

    @model_validator(mode="after")
    def _validate_interval(self) -> "PtsInterval":
        if self.out_pts <= self.in_pts:
            raise ValueError(f"out_pts ({self.out_pts}) must be > in_pts ({self.in_pts})")
        if self.time_base_num <= 0 or self.time_base_den <= 0:
            raise ValueError(
                f"time_base_num ({self.time_base_num}) and time_base_den ({self.time_base_den}) must be > 0"
            )
        tb = self.time_base
        if tb.numerator <= 0 or tb.denominator <= 0:
            raise ValueError(
                f"time_base numerator ({tb.numerator}) and denominator ({tb.denominator}) must be > 0"
            )
        return self

    @property
    def time_base(self) -> Fraction:
        return Fraction(self.time_base_num, self.time_base_den)

    @property
    def duration_seconds(self) -> float:
        tb = self.time_base
        return float((self.out_pts - self.in_pts) * tb)

    @property
    def in_seconds(self) -> float:
        return float(self.in_pts * self.time_base)

    @property
    def out_seconds(self) -> float:
        return float(self.out_pts * self.time_base)


# ---------------------------------------------------------------------------
# Stream info
# ---------------------------------------------------------------------------

class StreamInfo(BaseModel):
    index: int
    codec_type: str  # "video" | "audio" | "subtitle" | ...
    codec_name: str
    width: Optional[int] = None
    height: Optional[int] = None
    avg_frame_rate: Optional[str] = None   # stored as string, NEVER used for PTS math
    r_frame_rate: Optional[str] = None
    time_base_num: int = 1
    time_base_den: int = 1
    start_pts: Optional[int] = None
    duration_pts: Optional[int] = None
    nb_frames: Optional[int] = None
    is_vfr: bool = False                   # set by probe.py after PTS analysis
    extra: Dict[str, Any] = Field(default_factory=dict)

    @property
    def time_base(self) -> Fraction:
        return Fraction(self.time_base_num, self.time_base_den)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.duration_pts is None:
            return None
        return float(self.duration_pts * self.time_base)


# ---------------------------------------------------------------------------
# Source media record (B1: inherits CineBaseRecord; revision_id=None at probe time)
# ---------------------------------------------------------------------------

class SourceMediaRecord(CineBaseRecord):
    source_id: str = Field(default_factory=_new_id)  # type: ignore[assignment]
    path: str
    size_bytes: int
    mtime_ns: int
    sha256_head: str   # SHA-256 of first 256 KiB
    sha256_tail: str   # SHA-256 of last 256 KiB
    streams: List[StreamInfo] = Field(default_factory=list)
    duration_pts: Optional[int] = None   # from primary video stream
    start_pts: int = 0
    time_base_num: int = 1
    time_base_den: int = 1
    probe_raw: Dict[str, Any] = Field(default_factory=dict)   # full ffprobe output

    @property
    def time_base(self) -> Fraction:
        return Fraction(self.time_base_num, self.time_base_den)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.duration_pts is None:
            return None
        return float(self.duration_pts * self.time_base)


# ---------------------------------------------------------------------------
# Project record (B1: inherits CineBaseRecord)
# ---------------------------------------------------------------------------

class ProjectRecord(CineBaseRecord):
    project_id: str = Field(default_factory=_new_id)
    display_name: str
    source_id: Optional[str] = None  # type: ignore[assignment]
    current_revision: Optional[str] = None
    topic_id: Optional[str] = None
    mode: str = "film-analysis"
    extra: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Cut candidate
# ---------------------------------------------------------------------------

class CutCandidate(CineBaseRecord):
    candidate_id: str = Field(default_factory=_new_id)
    source_id: str  # type: ignore[assignment]   # required (not Optional) on CutCandidate
    revision_id: str  # type: ignore[assignment]  # required on CutCandidate
    stream_index: int
    pts: int                  # boundary PTS in source stream time_base
    time_base_num: int = 1
    time_base_den: int = 1
    detector: str             # e.g. "ContentDetector", "AdaptiveDetector", "manual"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    score: Optional[float] = None
    status: CandidateStatus = CandidateStatus.candidate
    detector_status: DetectorStatus = DetectorStatus.ok

    @property
    def time_base(self) -> Fraction:
        return Fraction(self.time_base_num, self.time_base_den)

    @property
    def pts_seconds(self) -> float:
        return float(self.pts * self.time_base)


# ---------------------------------------------------------------------------
# Evidence record (B11: extraction_status field; relative_path Optional)
# ---------------------------------------------------------------------------

class EvidenceRecord(CineBaseRecord):
    evidence_id: str = Field(default_factory=_new_id)
    source_id: str  # type: ignore[assignment]   # required on EvidenceRecord
    revision_id: str  # type: ignore[assignment]  # required on EvidenceRecord
    candidate_id: Optional[str] = None
    kind: str   # "frame_pre" | "frame_mid" | "frame_post" | "clip_boundary"
    relative_path: Optional[str] = None      # relative to revision dir; None when unavailable
    sha256: Optional[str] = None
    source_interval: Optional[PtsInterval] = None
    ffmpeg_argv: List[str] = Field(default_factory=list)
    tool_version: str = ""
    extraction_status: str = "ok"   # "ok" | "unavailable" | "failed"


# ---------------------------------------------------------------------------
# Source shot
# ---------------------------------------------------------------------------

class SourceShot(CineBaseRecord):
    shot_id: str = Field(default_factory=_new_id)
    source_id: str  # type: ignore[assignment]   # required on SourceShot
    revision_id: str  # type: ignore[assignment]  # required on SourceShot
    interval: PtsInterval
    candidate_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    observations: List[str] = Field(default_factory=list)
    verification: Dict[str, VerificationStatus] = Field(
        default_factory=lambda: {
            "boundary": VerificationStatus.unreviewed,
            "visual": VerificationStatus.unreviewed,
            "motion": VerificationStatus.unreviewed,
        }
    )


# ---------------------------------------------------------------------------
# Run state (B1: inherits CineBaseRecord; B8: stages_failed)
# ---------------------------------------------------------------------------

class RunState(CineBaseRecord):
    run_id: str = Field(default_factory=_new_id)
    project_id: str
    revision_id: str  # type: ignore[assignment]
    source_id: Optional[str] = None  # type: ignore[assignment]
    status: RunStatus = RunStatus.queued
    phase: str = "C0"
    source_sha256_head: Optional[str] = None
    source_sha256_tail: Optional[str] = None
    error: Optional[str] = None
    stages_complete: List[str] = Field(default_factory=list)
    stages_failed: List[str] = Field(default_factory=list)   # B8
    extra: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation result (B1: inherits CineBaseRecord)
# ---------------------------------------------------------------------------

class ValidationIssue(BaseModel):
    severity: str   # "error" | "warning"
    code: str
    message: str
    context: Dict[str, Any] = Field(default_factory=dict)


class ValidationResult(CineBaseRecord):
    result_id: str = Field(default_factory=_new_id)   # stable record ID
    revision_id: str  # type: ignore[assignment]
    source_id: Optional[str] = None  # type: ignore[assignment]
    passed: bool
    issues: List[ValidationIssue] = Field(default_factory=list)
