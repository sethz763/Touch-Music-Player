from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from engine.track import Track

@dataclass(slots=True)
class Cue:
    cue_id: str
    track: Track
    in_frame: int = 0
    out_frame: Optional[int] = None
    gain_db: float = 0.0
    loop_enabled: bool = False
    has_played: bool = False
    tod_start: Optional[datetime] = None
    # Track total duration in seconds if known
    total_seconds: Optional[float] = None
    # If True, this cue should be written to CSV/Excel when finished.
    logging_required: bool = False


@dataclass(frozen=True, slots=True)
class CueInfo:
    cue_id: str
    track_id: str
    file_path: str
    duration_seconds: Optional[float]
    in_frame: int = 0
    out_frame: Optional[int] = None
    gain_db: float = 0.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    metadata: dict | None = None
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    loop_enabled: bool = False
    removal_reason: str = ""  # Track why cue was removed (eof, manual_fade, auto_fade, error, forced)
    # If True, this cue should be written to CSV/Excel when finished.
    logging_required: bool = False