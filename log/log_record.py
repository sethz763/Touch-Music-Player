from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

@dataclass(frozen=True, slots=True)
class LogRecord:
    cue_id: str
    track_id: str
    tod_start: datetime
    source: str
    message: str
    metadata: Dict[str, Any]


@dataclass(frozen=True, slots=True)
class CueLogRecord:
    """LogRecord for a finished cue with all playback metadata."""
    cue_id: str
    track_id: str
    file_path: str
    started_at: datetime
    stopped_at: datetime
    duration_seconds: Optional[float]  # total track duration
    in_frame: int
    out_frame: Optional[int]
    gain_db: float
    fade_in_ms: int
    fade_out_ms: int
    reason: str  # "eof", "stopped", etc.
    metadata: Dict[str, Any]  # additional custom metadata

