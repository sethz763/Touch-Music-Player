"""Small wrappers around QFileDialog that remember the last-used directory.

Goal: make all file dialogs in the app open from a consistent folder (either a
fixed default or the last folder the user used), without duplicating logic.

We use QSettings with an explicit organization/app name so persistence works even
if the QApplication names aren't set elsewhere.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QWidget


_SETTINGS_ORG = "StepD"
_SETTINGS_APP = "StepD"


def _settings() -> QSettings:
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def _norm_dir(path: Optional[str]) -> str:
    if not path:
        return ""
    try:
        p = os.path.expanduser(str(path))
        p = os.path.abspath(p)
        return p
    except Exception:
        return ""


def _dir_of_file(path: str) -> str:
    try:
        d = os.path.dirname(path)
        return _norm_dir(d)
    except Exception:
        return ""


def get_open_file_name(
    parent: Optional[QWidget],
    caption: str,
    default_dir: str = "",
    file_filter: str = "",
    *,
    settings_key: str = "last_dir",
) -> Tuple[str, str]:
    """Like QFileDialog.getOpenFileName, but remembers last folder.

    Args:
        default_dir: Used if we don't have a remembered folder.
        settings_key: Where to store/read the last directory.
    """
    s = _settings()
    start_dir = _norm_dir(s.value(settings_key, "")) or _norm_dir(default_dir)

    filename, selected_filter = QFileDialog.getOpenFileName(
        parent,
        caption,
        start_dir,
        file_filter,
    )

    if filename:
        try:
            s.setValue(settings_key, _dir_of_file(filename))
        except Exception:
            pass

    return filename, selected_filter


def get_open_file_names(
    parent: Optional[QWidget],
    caption: str,
    default_dir: str = "",
    file_filter: str = "",
    *,
    settings_key: str = "last_dir",
) -> Tuple[list[str], str]:
    """Like QFileDialog.getOpenFileNames, but remembers last folder.

    Returns:
        (filenames, selected_filter)
    """
    s = _settings()
    start_dir = _norm_dir(s.value(settings_key, "")) or _norm_dir(default_dir)

    filenames, selected_filter = QFileDialog.getOpenFileNames(
        parent,
        caption,
        start_dir,
        file_filter,
    )

    if filenames:
        try:
            s.setValue(settings_key, _dir_of_file(str(filenames[0])))
        except Exception:
            pass

    return [str(f) for f in (filenames or [])], selected_filter


def get_save_file_name(
    parent: Optional[QWidget],
    caption: str,
    default_dir: str = "",
    file_filter: str = "",
    *,
    settings_key: str = "last_dir",
) -> Tuple[str, str]:
    """Like QFileDialog.getSaveFileName, but remembers last folder."""
    s = _settings()
    start_dir = _norm_dir(s.value(settings_key, "")) or _norm_dir(default_dir)

    filename, selected_filter = QFileDialog.getSaveFileName(
        parent,
        caption,
        start_dir,
        file_filter,
    )

    if filename:
        try:
            s.setValue(settings_key, _dir_of_file(filename))
        except Exception:
            pass

    return filename, selected_filter
