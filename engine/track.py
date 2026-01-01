from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Track:
    track_id: str
    file_path: str
    channels: int
    sample_rate: int
    duration_frames: int | None = None
    codec_info: str | None = None
