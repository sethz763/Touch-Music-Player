from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Central defaults (used as fallbacks when env vars and/or engine_tuning.json are absent).
DEFAULT_AUDIO_SERVICE_BLOCK_FRAMES = 2048
DEFAULT_DECODE_START_BLOCK_MULT = 4

DEFAULT_OUTPUT_TARGET_BLOCKS = 192
DEFAULT_OUTPUT_LOW_WATER_BLOCKS = 96
DEFAULT_OUTPUT_STARVE_WARN_FRAMES = 2048

# Safety clamp defaults for output_process. These can be overridden via
# STEPD_OUTPUT_MIN_TARGET_BLOCKS / STEPD_OUTPUT_MIN_LOW_WATER_BLOCKS.
DEFAULT_OUTPUT_MIN_TARGET_BLOCKS = 24
DEFAULT_OUTPUT_MIN_LOW_WATER_BLOCKS = 12

DEFAULT_DECODE_CHUNK_MULT = 16
DEFAULT_DECODE_DEFAULT_CHUNK_MIN_FRAMES = 4096
DEFAULT_DECODE_CHUNK_MIN_FRAMES = 1024
DEFAULT_DECODE_SLICE_MAX_FRAMES = 4096


@dataclass(frozen=True, slots=True)
class EngineTuning:
    """Loaded tuning values (mostly latency/buffering related)."""

    audio_service_block_frames: int | None = None

    # Engine -> decoder contract
    decode_start_block_frames_multiplier: int | None = None

    # Output buffering (interpreted by output_process)
    output_target_blocks: int | None = None
    output_low_water_blocks: int | None = None
    output_starve_warn_frames: int | None = None
    output_min_target_blocks: int | None = None
    output_min_low_water_blocks: int | None = None

    # Decoder chunking/slicing (interpreted by decode_process_pooled)
    decode_chunk_frames: int | None = None
    decode_chunk_min_frames: int | None = None
    decode_default_chunk_min_frames: int | None = None
    decode_chunk_multiplier: int | None = None
    decode_slice_max_frames: int | None = None


def _repo_root() -> Path:
    # engine/ is a direct child of repo root.
    return Path(__file__).resolve().parents[1]


def _tuning_path() -> Path:
    root = _repo_root()
    env = (os.environ.get("STEPD_ENGINE_TUNING_PATH") or "").strip()
    if env:
        p = Path(env)
        if not p.is_absolute():
            p = root / p
        return p
    return root / "engine_tuning.json"


def resolve_engine_tuning_path() -> Path:
    """Return the resolved path to the active tuning file.

    This is useful for diagnostics/logging (especially inside subprocesses).
    """

    return _tuning_path()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _get_int(obj: dict[str, Any], *keys: str) -> int | None:
    cur: Any = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    if cur is None:
        return None
    try:
        return int(cur)
    except Exception:
        return None


def load_engine_tuning() -> EngineTuning:
    data = _read_json(_tuning_path())
    if not data:
        return EngineTuning()

    return EngineTuning(
        audio_service_block_frames=_get_int(data, "audio_service", "block_frames"),
        decode_start_block_frames_multiplier=_get_int(data, "engine", "decode_start_block_frames_multiplier"),
        output_target_blocks=_get_int(data, "output", "target_blocks"),
        output_low_water_blocks=_get_int(data, "output", "low_water_blocks"),
        output_starve_warn_frames=_get_int(data, "output", "starve_warn_frames"),
        output_min_target_blocks=_get_int(data, "output", "min_target_blocks"),
        output_min_low_water_blocks=_get_int(data, "output", "min_low_water_blocks"),
        decode_chunk_frames=_get_int(data, "decode", "chunk_frames"),
        decode_chunk_min_frames=_get_int(data, "decode", "min_chunk_frames"),
        decode_default_chunk_min_frames=_get_int(data, "decode", "default_chunk_min_frames"),
        decode_chunk_multiplier=_get_int(data, "decode", "chunk_multiplier"),
        decode_slice_max_frames=_get_int(data, "decode", "slice_max_frames"),
    )


def _set_env_default(key: str, value: int | None, *, overwrite: bool) -> None:
    if value is None:
        return
    if not overwrite and (os.environ.get(key) or "").strip() != "":
        return
    os.environ[key] = str(int(value))


def apply_engine_tuning_to_env(*, overwrite: bool = False) -> EngineTuning:
    """Load engine_tuning.json and map supported settings onto env vars.

    Precedence:
    - If overwrite=False (default), existing env vars win.
    - Otherwise, tuning values forcibly replace env vars.

    Returns the parsed EngineTuning for callers that also want direct values.
    """

    # Allow callers to force JSON->env even when a parent process already set env.
    # This preserves the default precedence (env wins) unless explicitly opted into.
    try:
        if not overwrite:
            overwrite = bool(int((os.environ.get("STEPD_ENGINE_TUNING_OVERWRITE") or "0").strip() or "0"))
    except Exception:
        pass

    tuning = load_engine_tuning()

    _set_env_default("STEPD_OUTPUT_TARGET_BLOCKS", tuning.output_target_blocks, overwrite=overwrite)
    _set_env_default("STEPD_OUTPUT_LOW_WATER_BLOCKS", tuning.output_low_water_blocks, overwrite=overwrite)
    _set_env_default("STEPD_OUTPUT_STARVE_WARN_FRAMES", tuning.output_starve_warn_frames, overwrite=overwrite)
    _set_env_default("STEPD_OUTPUT_MIN_TARGET_BLOCKS", tuning.output_min_target_blocks, overwrite=overwrite)
    _set_env_default("STEPD_OUTPUT_MIN_LOW_WATER_BLOCKS", tuning.output_min_low_water_blocks, overwrite=overwrite)

    _set_env_default("STEPD_DECODE_CHUNK_FRAMES", tuning.decode_chunk_frames, overwrite=overwrite)
    _set_env_default("STEPD_DECODE_CHUNK_MIN_FRAMES", tuning.decode_chunk_min_frames, overwrite=overwrite)
    _set_env_default("STEPD_DECODE_DEFAULT_CHUNK_MIN_FRAMES", tuning.decode_default_chunk_min_frames, overwrite=overwrite)
    _set_env_default("STEPD_DECODE_CHUNK_MULT", tuning.decode_chunk_multiplier, overwrite=overwrite)
    _set_env_default("STEPD_DECODE_SLICE_MAX_FRAMES", tuning.decode_slice_max_frames, overwrite=overwrite)

    _set_env_default(
        "STEPD_DECODE_START_BLOCK_MULT",
        tuning.decode_start_block_frames_multiplier,
        overwrite=overwrite,
    )

    return tuning
