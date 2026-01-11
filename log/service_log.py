from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def get_repo_root() -> Path:
    """Best-effort repo root.

    This file lives at <root>/log/service_log.py.
    """
    try:
        return Path(__file__).resolve().parents[1]
    except Exception:
        return Path.cwd()


def get_runtime_root() -> Path:
    """Return the runtime base directory.

    - In PyInstaller/frozen builds: directory containing the executable
    - In normal dev runs: repo root
    """
    try:
        if bool(getattr(sys, "frozen", False)):
            return Path(sys.executable).resolve().parent
    except Exception:
        pass
    return get_repo_root()


def get_service_log_dir() -> Path:
    """Return the directory where all service logs should be written.

    Defaults to <exe_dir>/service_logs when frozen (PyInstaller),
    otherwise <repo_root>/service_logs.
    Override with STEPD_SERVICE_LOG_DIR.
    """
    override = None
    try:
        override = os.environ.get("STEPD_SERVICE_LOG_DIR")
    except Exception:
        override = None

    base: Path
    if override:
        try:
            base = Path(str(override))
            if not base.is_absolute():
                base = (get_runtime_root() / base)
        except Exception:
            base = get_runtime_root() / "service_logs"
    else:
        base = get_runtime_root() / "service_logs"

    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base


def _safe_filename(name: str) -> str:
    name = str(name or "").strip() or "service.log"
    # Remove path separators and other surprising characters.
    name = name.replace("/", "_").replace("\\", "_")
    name = "".join(ch for ch in name if (ch.isalnum() or ch in ("-", "_", ".")))
    return name or "service.log"


def _is_within_dir(path: Path, parent: Path) -> bool:
    try:
        path_r = path.resolve()
        parent_r = parent.resolve()
        return path_r == parent_r or parent_r in path_r.parents
    except Exception:
        return False


def coerce_log_path(
    *,
    env_value: Optional[str],
    default_filename: str,
    allow_absolute_outside_service_dir: bool = False,
) -> Path:
    """Compute a log file path that lives under service_logs/ by default.

    Rules:
        - If env_value is empty: <service_logs>/<default_filename>
        - If env_value is relative: <service_logs>/<env_value>
    - If env_value points to a directory (existing dir or ends with / or \\):
            <that_dir>/<default_filename> (dir is relative to service_logs if not absolute)
    - If env_value is absolute: use it ONLY if allow_absolute_outside_service_dir is True,
            otherwise force it under service_logs using its basename.

    This keeps production behavior predictable while still allowing dev overrides.
    """
    base = get_service_log_dir()

    if not env_value:
        return (base / _safe_filename(default_filename)).resolve()

    raw = str(env_value)
    try:
        p = Path(raw)
    except Exception:
        return (base / _safe_filename(default_filename)).resolve()

    # Normalize relative to service_logs.
    if not p.is_absolute():
        p = base / p

    # If treated as a directory, append default filename.
    try:
        if raw.endswith(("/", "\\")) or (p.exists() and p.is_dir()):
            p = p / _safe_filename(default_filename)
    except Exception:
        pass

    # Enforce service_logs containment unless explicitly allowed.
    if not allow_absolute_outside_service_dir:
        try:
            candidate = p.resolve()
        except Exception:
            candidate = p
        if not _is_within_dir(candidate, base):
            candidate = (base / _safe_filename(candidate.name)).resolve()
        p = candidate

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    return p
