import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional


class SaveSettings:
    def __init__(
        self,
        file_path: str | os.PathLike[str],
        *,
        autosave: bool = False,
        debounce_seconds: float = 0.5,
    ):
        self.file_path = Path(file_path)
        self.settings: dict[str, Any] = {}

        self._lock = threading.Lock()
        self._autosave = bool(autosave)
        self._debounce_seconds = float(debounce_seconds)
        self._save_timer: Optional[threading.Timer] = None
        self._last_save_ts: float = 0.0

        self.load_settings()

    def load_settings(self) -> None:
        with self._lock:
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                # Best-effort: if parent isn't creatable (e.g. empty relative path), continue.
                pass

            if not self.file_path.exists():
                # If file doesn't exist make a blank dictionary.
                print("settings file doesn't exist...creating one")
                self.settings = {}
                # Save without debouncing so a file exists immediately.
                self._save_settings_unlocked()
                return

            try:
                with self.file_path.open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
                self.settings = loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError:
                # If the settings file is corrupted (e.g. app crash mid-write), reset safely.
                try:
                    os.replace(self.file_path, self.file_path.with_suffix(self.file_path.suffix + ".corrupt"))
                except Exception:
                    pass
                self.settings = {}
                self._save_settings_unlocked()
            except FileNotFoundError:
                self.settings = {}
                self._save_settings_unlocked()
            except Exception as e:
                print(f"Failed to load settings from {self.file_path}: {e}")
                self.settings = {}

    def enable_autosave(self, *, debounce_seconds: Optional[float] = None) -> None:
        with self._lock:
            self._autosave = True
            if debounce_seconds is not None:
                self._debounce_seconds = float(debounce_seconds)

    def disable_autosave(self) -> None:
        with self._lock:
            self._autosave = False
            timer = self._save_timer
            self._save_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def schedule_save(self, *, debounce_seconds: Optional[float] = None) -> None:
        """Debounced save.

        Safe to call frequently (e.g. sliders, rapid button edits).
        """
        with self._lock:
            delay = float(self._debounce_seconds if debounce_seconds is None else debounce_seconds)
            existing = self._save_timer
            self._save_timer = None

            if delay <= 0:
                self._save_settings_unlocked()
                return

            timer = threading.Timer(delay, self._debounced_save)
            timer.daemon = True
            self._save_timer = timer

        if existing is not None:
            try:
                existing.cancel()
            except Exception:
                pass

        try:
            timer.start()
        except Exception:
            # Fall back to immediate save if timer can't start.
            self.save_settings()

    def _debounced_save(self) -> None:
        with self._lock:
            # Timer fired; clear handle before writing.
            self._save_timer = None
            self._save_settings_unlocked()

    def save_settings(self) -> None:
        with self._lock:
            timer = self._save_timer
            self._save_timer = None

            if timer is not None:
                try:
                    timer.cancel()
                except Exception:
                    pass

            self._save_settings_unlocked()

    def _save_settings_unlocked(self) -> None:
        try:
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            tmp_path = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as file:
                json.dump(self.settings, file, indent=4, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())

            # Atomic replace on Windows and POSIX.
            os.replace(tmp_path, self.file_path)
            self._last_save_ts = time.time()
        except Exception as e:
            # Keep settings persistence best-effort, but don't silently swallow failures.
            print(f"Failed to save settings to {self.file_path}: {e}")

    def get_setting(self, key: str, default: Optional[Any] = None) -> Any:
        try:
            return self.settings.get(key, default)
        except Exception:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self.settings[key] = value
            autosave = self._autosave

        if autosave:
            self.schedule_save()

    def get_settings(self) -> dict[str, Any]:
        # Return the live dict for backward compatibility.
        return self.settings

    def replace_settings(self, new_settings: dict[str, Any], *, save: bool = True) -> None:
        """Replace the entire settings dict.

        Useful for bulk loads (e.g. project import) without lots of per-key calls.
        """
        with self._lock:
            self.settings = dict(new_settings or {})
            autosave = bool(self._autosave)

        if not save:
            return

        if autosave:
            self.schedule_save()
        else:
            self.save_settings()

    def delete_settings(self, key: str) -> None:
        with self._lock:
            try:
                self.settings.pop(key, None)
            except Exception:
                pass
            autosave = self._autosave

        if autosave:
            self.schedule_save()

    def flush(self) -> None:
        """Force any pending debounced save to be written now."""
        self.save_settings()

    def close(self) -> None:
        """Alias for flush(), for callers that want an explicit lifecycle hook."""
        self.flush()