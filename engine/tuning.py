from __future__ import annotations

import json
import os
import sys
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


def _is_frozen() -> bool:
    # PyInstaller sets sys.frozen and sys._MEIPASS.
    return bool(getattr(sys, "frozen", False))


def _exe_dir() -> Path | None:
    if not _is_frozen():
        return None
    try:
        return Path(sys.executable).resolve().parent
    except Exception:
        return None


def _meipass_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    try:
        return Path(base).resolve()
    except Exception:
        return None


def _autocreate_enabled() -> bool:
    # Default to enabled for frozen builds; allow disabling via env var.
    raw = (os.environ.get("STEPD_ENGINE_TUNING_AUTOCREATE") or "1").strip()
    try:
        return bool(int(raw or "1"))
    except Exception:
        return True


def _ensure_default_tuning_next_to_exe() -> Path | None:
    """Create engine_tuning.json next to the EXE (frozen builds only).

    This is best-effort: failures (e.g. no write permission) are ignored.
    """

    if not _is_frozen() or not _autocreate_enabled():
        return None

    exe_dir = _exe_dir()
    if exe_dir is None:
        return None

    dst = exe_dir / "engine_tuning.json"
    if dst.exists():
        return dst

    src_dir = _meipass_dir()
    if src_dir is None:
        return None
    src = src_dir / "engine_tuning.json"
    if not src.exists():
        return None

    try:
        # Avoid partial writes: write then replace.
        tmp = dst.with_suffix(".json.tmp")
        tmp.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        tmp.replace(dst)
        return dst
    except Exception:
        return None


def _base_dir_for_relative_paths() -> Path:
    # For a frozen app, relative paths should resolve next to the executable
    # so you can drop in an engine_tuning.json beside the EXE.
    return _exe_dir() or _repo_root()


def _tuning_path() -> Path:
    root = _repo_root()
    env = (os.environ.get("STEPD_ENGINE_TUNING_PATH") or "").strip()
    if env:
        p = Path(env)
        if not p.is_absolute():
            p = _base_dir_for_relative_paths() / p
        return p

    # Default resolution order:
    # 1) If frozen: allow overriding by placing engine_tuning.json next to the EXE
    # 2) If frozen: fall back to the bundled copy in _MEIPASS (if included as data)
    # 3) Dev: repo root
    if _is_frozen():
        # If possible, materialize a default config next to the EXE.
        created = _ensure_default_tuning_next_to_exe()
        if created is not None:
            return created

        exe_dir = _exe_dir()
        if exe_dir is not None:
            candidate = exe_dir / "engine_tuning.json"
            if candidate.exists():
                return candidate

        mei_dir = _meipass_dir()
        if mei_dir is not None:
            candidate = mei_dir / "engine_tuning.json"
            if candidate.exists():
                return candidate

        # If neither exists, return the expected external location for diagnostics.
        if exe_dir is not None:
            return exe_dir / "engine_tuning.json"

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
