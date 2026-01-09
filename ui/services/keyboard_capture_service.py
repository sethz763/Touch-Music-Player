"""Keyboard capture service (Qt focus events + optional global capture).

Goal
----
Provide a single, Qt-friendly event stream for keyboard input that can be driven
by either:
- Qt key events (only when app/window has focus)
- Global key events (works when app is not focused)

Global capture backends
----------------------
- Windows/macOS: typically works well with `pynput`.
- Linux X11: `pynput` generally works.
- Linux Wayland: global capture via `pynput` is often restricted by design.
  For global capture on Wayland, the most reliable option is `evdev` (reads
  kernel input events from /dev/input/event*), which commonly requires extra
  permissions (input group / udev rules / root).

This module is UI-layer only:
- It depends on PySide6.
- Do NOT import it from engine/*.

Intended integration (next step)
--------------------------------
- Instantiate one `KeyboardCaptureService` from the GUI layer.
- Wire settings UI controls to call `set_mode(...)` and `set_backend_preference(...)`.
- Subscribe to `key_event` to trigger your hotkey/shortcut actions.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets


class KeyboardCaptureMode(str, Enum):
    """How the application should capture keyboard input."""

    FOCUS_ONLY = "focus_only"  # Qt events only
    GLOBAL = "global"  # use a global backend (pynput/evdev)


class GlobalBackendPreference(str, Enum):
    """Which global backend to use when mode == GLOBAL."""

    AUTO = "auto"
    PYNPUT = "pynput"
    EVDEV = "evdev"  # Linux-only


@dataclass(frozen=True)
class KeyboardEvent:
    """Normalized keyboard event emitted by the service."""

    source: str  # "qt" | "pynput" | "evdev"
    action: str  # "press" | "release" | "hold"

    # Human-friendly description, good for logs/debug UI.
    text: str

    # Optional low-level identifiers for distinguishing keys (e.g., numpad).
    char: str | None = None
    vk: int | None = None
    scan_code: int | None = None

    # Qt-specific.
    qt_key: int | None = None
    qt_modifiers: int | None = None
    is_auto_repeat: bool = False

    # evdev-specific.
    evdev_code: int | None = None
    evdev_name: str | None = None
    device_name: str | None = None

    timestamp: float = 0.0


class KeyboardCaptureStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    DEGRADED = "degraded"  # running but missing capability/permission
    ERROR = "error"


class KeyboardCaptureService(QtCore.QObject):
    """Owns keyboard capture backends and emits normalized key events."""

    key_event = QtCore.Signal(object)  # KeyboardEvent
    status_changed = QtCore.Signal(str, str)  # status, message

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._mode: KeyboardCaptureMode = KeyboardCaptureMode.FOCUS_ONLY
        self._backend_pref: GlobalBackendPreference = GlobalBackendPreference.AUTO

        self._qt_event_filter_installed = False
        self._qt_target: QtCore.QObject | None = None

        self._global_backend: _GlobalBackend | None = None

        self._status: KeyboardCaptureStatus = KeyboardCaptureStatus.STOPPED
        self._status_message: str = ""

    # ----- Public API -----

    def attach_qt_target(self, target: QtCore.QObject) -> None:
        """Install an event filter on a Qt object (usually QApplication).

        Call this once from the GUI thread.

        Recommended target: `QApplication.instance()`.
        """

        if self._qt_target is target and self._qt_event_filter_installed:
            return

        if self._qt_target is not None and self._qt_event_filter_installed:
            try:
                self._qt_target.removeEventFilter(self)
            except Exception:
                pass

        self._qt_target = target
        try:
            target.installEventFilter(self)
            self._qt_event_filter_installed = True
        except Exception as e:
            self._qt_event_filter_installed = False
            self._emit_status(KeyboardCaptureStatus.ERROR, f"Failed to install Qt event filter: {e}")

    def set_mode(self, mode: KeyboardCaptureMode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self._apply_mode()

    def set_backend_preference(self, pref: GlobalBackendPreference) -> None:
        if pref == self._backend_pref:
            return
        self._backend_pref = pref
        if self._mode == KeyboardCaptureMode.GLOBAL:
            self._restart_global_backend()

    def start(self) -> None:
        """Start capture according to current settings."""
        self._apply_mode()

    def stop(self) -> None:
        """Stop all capture (Qt filter remains installed but ignored)."""

        self._stop_global_backend()
        self._emit_status(KeyboardCaptureStatus.STOPPED, "")

    def status(self) -> tuple[KeyboardCaptureStatus, str]:
        return self._status, self._status_message

    # ----- Qt event filter -----

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        if event.type() not in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease):
            return super().eventFilter(watched, event)

        if not isinstance(event, QtGui.QKeyEvent):
            return super().eventFilter(watched, event)

        action = "press" if event.type() == QtCore.QEvent.Type.KeyPress else "release"

        text = _format_qt_key(event)
        is_auto_repeat = False
        try:
            if getattr(event, "isAutoRepeat", None) and event.isAutoRepeat():
                is_auto_repeat = True
        except Exception:
            is_auto_repeat = False

        try:
            qt_key = _safe_int(event.key())
        except Exception:
            qt_key = None

        try:
            qt_mods = _safe_int(event.modifiers())
        except Exception:
            qt_mods = None

        # Normalize Qt's numpad digit key variants (some platforms report
        # Key_Numpad1/Keypad1, others report Key_1 with KeypadModifier).
        # Canonical form used across the app: Key_0..Key_9 plus KeypadModifier.
        try:
            digit = _qt_numpad_digit_from_key(qt_key)
            if digit is not None:
                qt_key = int(QtCore.Qt.Key.Key_0) + int(digit)
                qt_mods = int(qt_mods or 0) | int(_safe_int(QtCore.Qt.KeyboardModifier.KeypadModifier) or 0)
        except Exception:
            pass

        # Best-effort: add KeypadModifier for numpad keys.
        # Qt does not always set KeypadModifier consistently across platforms.
        try:
            _vk_attr = getattr(event, 'nativeVirtualKey', None)
            vk = int(_vk_attr() if callable(_vk_attr) else (_vk_attr or 0))
        except Exception:
            vk = 0
        try:
            _sc_attr = getattr(event, 'nativeScanCode', None)
            sc = int(_sc_attr() if callable(_sc_attr) else (_sc_attr or 0))
        except Exception:
            sc = 0

        try:
            mods_int = int(qt_mods or 0)
            # Windows: VK_NUMPAD0..9 and keypad ops: 0x60..0x6F
            if sys.platform.startswith('win') and 0x60 <= int(vk) <= 0x6F:
                mods_int |= int(_safe_int(QtCore.Qt.KeyboardModifier.KeypadModifier) or 0)
            # Windows fallback: scan codes for numpad digits.
            if sys.platform.startswith('win') and int(sc) in {0x47, 0x48, 0x49, 0x4B, 0x4C, 0x4D, 0x4F, 0x50, 0x51, 0x52}:
                mods_int |= int(_safe_int(QtCore.Qt.KeyboardModifier.KeypadModifier) or 0)
            # macOS: common Apple virtual keycodes for keypad digits.
            if sys.platform == 'darwin' and int(vk) in {82, 83, 84, 85, 86, 87, 88, 89, 91, 92}:
                mods_int |= int(_safe_int(QtCore.Qt.KeyboardModifier.KeypadModifier) or 0)
            # Linux (X11/Wayland): often exposes keypad keysyms in nativeVirtualKey.
            if sys.platform.startswith('linux') and 0xFFB0 <= int(vk) <= 0xFFBF:
                mods_int |= int(_safe_int(QtCore.Qt.KeyboardModifier.KeypadModifier) or 0)
            qt_mods = int(mods_int)
        except Exception:
            pass

        ev = KeyboardEvent(
            source="qt",
            action=action,
            text=f"Qt: {action} {text}",
            char=None,
            vk=int(vk) if vk else None,
            scan_code=int(sc) if sc else None,
            qt_key=qt_key,
            qt_modifiers=qt_mods,
            is_auto_repeat=bool(is_auto_repeat),
            timestamp=time.time(),
        )
        self.key_event.emit(ev)
        return super().eventFilter(watched, event)

    # ----- Internal mode/backend control -----

    def _apply_mode(self) -> None:
        if self._mode == KeyboardCaptureMode.FOCUS_ONLY:
            self._stop_global_backend()
            self._emit_status(KeyboardCaptureStatus.RUNNING, "Qt focus-only capture active")
            return

        # GLOBAL
        self._start_or_restart_global_backend()

    def _start_or_restart_global_backend(self) -> None:
        if self._global_backend is not None and self._global_backend.is_running:
            return
        self._restart_global_backend()

    def _restart_global_backend(self) -> None:
        self._stop_global_backend()

        backend = _select_global_backend(self._backend_pref)
        if backend is None:
            self._emit_status(
                KeyboardCaptureStatus.DEGRADED,
                "Global capture not available (missing deps or unsupported platform).",
            )
            return

        self._global_backend = backend
        backend.key_event.connect(self.key_event.emit)
        backend.status_changed.connect(self._emit_status_from_backend)
        backend.start()

    def _stop_global_backend(self) -> None:
        backend = self._global_backend
        self._global_backend = None
        if backend is None:
            return
        try:
            backend.stop()
        except Exception:
            pass

    @QtCore.Slot(str, str)
    def _emit_status_from_backend(self, status: str, message: str) -> None:
        try:
            st = KeyboardCaptureStatus(status)
        except Exception:
            st = KeyboardCaptureStatus.ERROR
        self._emit_status(st, message)

    def _emit_status(self, status: KeyboardCaptureStatus, message: str) -> None:
        self._status = status
        self._status_message = message
        self.status_changed.emit(status.value, message)


# ----------------- Helpers -----------------


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError:
        try:
            return int(getattr(value, "value"))
        except Exception:
            return None


def _qt_numpad_digit_from_key(qt_key: int | None) -> int | None:
    """Return 0-9 if qt_key corresponds to a numpad digit key."""

    if qt_key is None:
        return None

    try:
        k = int(qt_key)
    except Exception:
        return None

    # Some Qt builds expose dedicated numpad keys. We normalize those into
    # (Key_0..Key_9 + KeypadModifier) elsewhere.
    for d in range(10):
        for attr in (f"Key_Numpad{d}", f"Keypad{d}"):
            try:
                v = getattr(QtCore.Qt.Key, attr, None)
                if v is not None and int(v) == k:
                    return int(d)
            except Exception:
                continue

    return None


def _format_qt_key(event: QtGui.QKeyEvent) -> str:
    mods_int = _safe_int(event.modifiers()) or 0
    key_int = _safe_int(event.key()) or 0

    seq = QtGui.QKeySequence(mods_int | key_int)
    text = seq.toString(QtGui.QKeySequence.SequenceFormat.NativeText)
    if not text:
        text = event.text() or f"Key({key_int})"
    if event.isAutoRepeat():
        text += " (auto-repeat)"
    return text


def _is_linux_wayland() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _is_linux_x11() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    # Heuristic: if DISPLAY exists and WAYLAND_DISPLAY is absent.
    return bool(os.environ.get("DISPLAY")) and not bool(os.environ.get("WAYLAND_DISPLAY"))


# ----------------- Global backends -----------------


class _GlobalBackend(QtCore.QObject):
    key_event = QtCore.Signal(object)  # KeyboardEvent
    status_changed = QtCore.Signal(str, str)  # status, message

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.is_running: bool = False

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


def _select_global_backend(pref: GlobalBackendPreference) -> _GlobalBackend | None:
    # Explicit preference.
    if pref == GlobalBackendPreference.EVDEV:
        return _EvdevBackend.try_create()
    if pref == GlobalBackendPreference.PYNPUT:
        return _PynputBackend.try_create()

    # AUTO policy.
    if _is_linux_wayland():
        # Prefer evdev first.
        return _EvdevBackend.try_create() or _PynputBackend.try_create()

    # Windows/macOS/Linux-X11 default to pynput.
    return _PynputBackend.try_create() or _EvdevBackend.try_create()


class _PynputBackend(_GlobalBackend):
    def __init__(self) -> None:
        super().__init__()
        self._listener: Any | None = None
        self._active_mods: set[str] = set()
        self._keyboard_mod: Any | None = None

    @staticmethod
    def try_create() -> _PynputBackend | None:
        try:
            import pynput  # noqa: F401
        except Exception:
            return None
        return _PynputBackend()

    def start(self) -> None:
        if self.is_running:
            return

        try:
            from pynput import keyboard  # type: ignore
        except Exception as e:
            self.status_changed.emit(KeyboardCaptureStatus.ERROR.value, f"pynput import failed: {e}")
            return

        self._keyboard_mod = keyboard

        def on_press(key: Any) -> None:
            self._update_pynput_mod_state("press", key)
            ev = _pynput_to_event("press", key, active_mods=set(self._active_mods), keyboard_mod=self._keyboard_mod)
            self.key_event.emit(ev)

        def on_release(key: Any) -> None:
            # Note: generate event using modifier state *before* clearing, so a
            # modifier-release has a consistent "mods" view.
            ev = _pynput_to_event("release", key, active_mods=set(self._active_mods), keyboard_mod=self._keyboard_mod)
            self.key_event.emit(ev)
            self._update_pynput_mod_state("release", key)

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()
        self.is_running = True

        if _is_linux_wayland():
            self.status_changed.emit(
                KeyboardCaptureStatus.DEGRADED.value,
                "pynput started, but Wayland may block global capture; evdev is recommended.",
            )
        else:
            self.status_changed.emit(KeyboardCaptureStatus.RUNNING.value, "Global capture via pynput")

    def stop(self) -> None:
        self.is_running = False
        self._active_mods.clear()
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass

    def _update_pynput_mod_state(self, action: str, key: Any) -> None:
        try:
            keyboard_mod = self._keyboard_mod
            if keyboard_mod is None:
                return
            mod_name = _pynput_modifier_name(keyboard_mod, key)
            if not mod_name:
                return
            if action == "press":
                self._active_mods.add(mod_name)
            elif action == "release":
                self._active_mods.discard(mod_name)
        except Exception:
            return


def _pynput_to_event(action: str, key: Any, *, active_mods: set[str], keyboard_mod: Any | None) -> KeyboardEvent:
    char = getattr(key, "char", None)
    vk = _safe_int(getattr(key, "vk", None))
    sc = _safe_int(getattr(key, "scan_code", None))

    # repr(key) varies by backend; keep it as a useful debug field.
    key_text = _format_pynput_key_text(key)

    qt_mods = _mods_to_qt_modifiers(active_mods)
    qt_key = _pynput_key_to_qt_key(keyboard_mod, key)

    # Distinguish numpad keys when the platform exposes enough information.
    try:
        if _pynput_is_keypad(key):
            qt_mods = int(qt_mods) | int(_safe_int(QtCore.Qt.KeyboardModifier.KeypadModifier) or 0)
    except Exception:
        pass

    return KeyboardEvent(
        source="pynput",
        action=action,
        text=f"pynput: {action} {key_text}",
        char=char,
        vk=vk,
        scan_code=sc,
        qt_key=qt_key,
        qt_modifiers=qt_mods,
        timestamp=time.time(),
    )


def _format_pynput_key_text(key: Any) -> str:
    # Try to present char/vk/sc consistently for numpad disambiguation.
    parts: list[str] = []

    char = getattr(key, "char", None)
    if char is not None:
        parts.append(f"char={repr(char)}")

    vk = _safe_int(getattr(key, "vk", None))
    if vk is not None:
        parts.append(f"vk=0x{vk:02X}({vk})")

    sc = _safe_int(getattr(key, "scan_code", None))
    if sc is not None:
        parts.append(f"sc={sc}")

    if parts:
        return " ".join(parts)

    try:
        return str(key)
    except Exception:
        return repr(key)


def _mods_to_qt_modifiers(active_mods: set[str]) -> int:
    mods = 0
    try:
        if "shift" in active_mods:
            mods |= int(_safe_int(QtCore.Qt.KeyboardModifier.ShiftModifier) or 0)
        if "ctrl" in active_mods:
            mods |= int(_safe_int(QtCore.Qt.KeyboardModifier.ControlModifier) or 0)
        if "alt" in active_mods:
            mods |= int(_safe_int(QtCore.Qt.KeyboardModifier.AltModifier) or 0)
        if "meta" in active_mods:
            mods |= int(_safe_int(QtCore.Qt.KeyboardModifier.MetaModifier) or 0)
    except Exception:
        pass
    return int(mods)


def _pynput_modifier_name(keyboard_mod: Any, key: Any) -> str | None:
    """Return normalized modifier name for a pynput key, or None."""
    try:
        k = key
        # Typical values: keyboard.Key.shift, shift_l, shift_r, ctrl, ctrl_l, etc.
        if k in (
            keyboard_mod.Key.shift,
            getattr(keyboard_mod.Key, "shift_l", None),
            getattr(keyboard_mod.Key, "shift_r", None),
        ):
            return "shift"
        if k in (
            keyboard_mod.Key.ctrl,
            getattr(keyboard_mod.Key, "ctrl_l", None),
            getattr(keyboard_mod.Key, "ctrl_r", None),
        ):
            return "ctrl"
        if k in (
            keyboard_mod.Key.alt,
            getattr(keyboard_mod.Key, "alt_l", None),
            getattr(keyboard_mod.Key, "alt_r", None),
            getattr(keyboard_mod.Key, "alt_gr", None),
        ):
            return "alt"
        if k in (
            getattr(keyboard_mod.Key, "cmd", None),
            getattr(keyboard_mod.Key, "cmd_l", None),
            getattr(keyboard_mod.Key, "cmd_r", None),
            keyboard_mod.Key.meta,
            getattr(keyboard_mod.Key, "meta_l", None),
            getattr(keyboard_mod.Key, "meta_r", None),
            getattr(keyboard_mod.Key, "super", None),
        ):
            return "meta"
    except Exception:
        return None
    return None


def _pynput_key_to_qt_key(keyboard_mod: Any | None, key: Any) -> int | None:
    """Best-effort mapping from pynput key to a Qt key code."""

    if keyboard_mod is None:
        return None

    try:
        # Modifier keys
        mod = _pynput_modifier_name(keyboard_mod, key)
        if mod == "shift":
            return int(QtCore.Qt.Key.Key_Shift)
        if mod == "ctrl":
            return int(QtCore.Qt.Key.Key_Control)
        if mod == "alt":
            return int(QtCore.Qt.Key.Key_Alt)
        if mod == "meta":
            return int(QtCore.Qt.Key.Key_Meta)

        # Special keys (non-exhaustive)
        if key == keyboard_mod.Key.space:
            return int(QtCore.Qt.Key.Key_Space)
        if key in (keyboard_mod.Key.enter, getattr(keyboard_mod.Key, "return", None)):
            return int(QtCore.Qt.Key.Key_Return)
        if key == keyboard_mod.Key.esc:
            return int(QtCore.Qt.Key.Key_Escape)
        if key == keyboard_mod.Key.tab:
            return int(QtCore.Qt.Key.Key_Tab)
        if key == keyboard_mod.Key.backspace:
            return int(QtCore.Qt.Key.Key_Backspace)
        if key == keyboard_mod.Key.delete:
            return int(QtCore.Qt.Key.Key_Delete)
        if key == keyboard_mod.Key.up:
            return int(QtCore.Qt.Key.Key_Up)
        if key == keyboard_mod.Key.down:
            return int(QtCore.Qt.Key.Key_Down)
        if key == keyboard_mod.Key.left:
            return int(QtCore.Qt.Key.Key_Left)
        if key == keyboard_mod.Key.right:
            return int(QtCore.Qt.Key.Key_Right)

        # KeyCode -> use vk/char when possible
        vk = _safe_int(getattr(key, "vk", None))
        if vk is not None:
            # Windows: VK_NUMPAD0..9 (0x60..0x69)
            if 0x60 <= int(vk) <= 0x69:
                return int(QtCore.Qt.Key.Key_0) + (int(vk) - 0x60)

            # macOS (common): keypad digits keycodes 82..92 with a gap at 90.
            mac_map = {
                82: 0,
                83: 1,
                84: 2,
                85: 3,
                86: 4,
                87: 5,
                88: 6,
                89: 7,
                91: 8,
                92: 9,
            }
            if int(vk) in mac_map:
                return int(QtCore.Qt.Key.Key_0) + int(mac_map[int(vk)])

        char = getattr(key, "char", None)
        if isinstance(char, str) and len(char) == 1:
            if char.isalpha():
                base = int(QtCore.Qt.Key.Key_A)
                return base + (ord(char.upper()) - ord("A"))
            if char.isdigit():
                return int(QtCore.Qt.Key.Key_0) + int(char)

            # Basic punctuation we care about for shortcuts
            if char == "-":
                return int(QtCore.Qt.Key.Key_Minus)
            if char == "=":
                return int(QtCore.Qt.Key.Key_Equal)
            if char == "/":
                return int(QtCore.Qt.Key.Key_Slash)
            if char == "\\":
                return int(QtCore.Qt.Key.Key_Backslash)

    except Exception:
        return None

    return None


def _pynput_is_keypad(key: Any) -> bool:
    """Heuristic: detect whether a pynput key likely came from the numpad."""

    vk = _safe_int(getattr(key, "vk", None))
    if vk is None:
        return False

    # Windows: VK_NUMPAD0..9 and keypad operations are 0x60..0x6F.
    if sys.platform.startswith("win"):
        return 0x60 <= int(vk) <= 0x6F

    # macOS: common Apple virtual keycodes for keypad digits.
    if sys.platform == "darwin":
        return int(vk) in {82, 83, 84, 85, 86, 87, 88, 89, 91, 92}

    # Linux (X11/Wayland): often exposes XKB/X11 keysyms.
    # XK_KP_0..XK_KP_9 are 0xFFB0..0xFFB9; include common keypad operations too.
    if sys.platform.startswith("linux"):
        return 0xFFB0 <= int(vk) <= 0xFFBF

    return False


class _EvdevBackend(_GlobalBackend):
    def __init__(self, evdev_mod: Any, ecodes_mod: Any) -> None:
        super().__init__()
        self._evdev = evdev_mod
        self._ecodes = ecodes_mod

        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._devices: list[Any] = []
        self._active_mods: set[str] = set()

    @staticmethod
    def try_create() -> _EvdevBackend | None:
        if not sys.platform.startswith("linux"):
            return None
        try:
            import evdev  # type: ignore
            from evdev import ecodes  # type: ignore
        except Exception:
            return None
        return _EvdevBackend(evdev, ecodes)

    def start(self) -> None:
        if self.is_running:
            return

        self._stop.clear()
        self.is_running = True

        try:
            paths = list(self._evdev.list_devices())
        except Exception as e:
            self.status_changed.emit(KeyboardCaptureStatus.ERROR.value, f"evdev init failed: {e}")
            self.is_running = False
            return

        opened_any = False

        for path in paths:
            if self._stop.is_set():
                break

            try:
                dev = self._evdev.InputDevice(path)
                caps = dev.capabilities(verbose=False)
                if self._ecodes.EV_KEY not in caps:
                    try:
                        dev.close()
                    except Exception:
                        pass
                    continue
            except Exception:
                # permission denied is common; ignore and continue
                continue

            opened_any = True
            self._devices.append(dev)

            t = threading.Thread(
                target=self._read_loop,
                args=(dev,),
                daemon=True,
                name=f"evdev-{getattr(dev, 'name', 'kbd')}",
            )
            self._threads.append(t)
            t.start()

        if not opened_any:
            self.status_changed.emit(
                KeyboardCaptureStatus.DEGRADED.value,
                "evdev found no readable keyboard devices. "
                "You may need /dev/input permissions (input group / udev rules / root).",
            )
        else:
            self.status_changed.emit(
                KeyboardCaptureStatus.RUNNING.value,
                "Global capture via evdev (Linux). Numpad should appear as KEY_KP*.",
            )

    def stop(self) -> None:
        self.is_running = False
        self._active_mods.clear()
        self._stop.set()

        for dev in list(self._devices):
            try:
                dev.close()
            except Exception:
                pass

        self._devices.clear()

    def _read_loop(self, dev: Any) -> None:
        try:
            for event in dev.read_loop():
                if self._stop.is_set():
                    break

                if event.type != self._ecodes.EV_KEY:
                    continue

                if event.value == 1:
                    action = "press"
                elif event.value == 0:
                    action = "release"
                else:
                    action = "hold"

                key_name = self._ecodes.KEY.get(event.code, f"KEY_{event.code}")
                dev_name = getattr(dev, "name", None)

                # Update modifier state before emitting (press), and after emitting (release)
                # to mirror typical "mods include key being pressed" semantics.
                if action == "press":
                    _update_evdev_mods(self._active_mods, key_name, True)

                qt_mods = _mods_to_qt_modifiers(self._active_mods)
                qt_key = _evdev_name_to_qt_key(key_name)

                # Distinguish numpad keys.
                try:
                    if key_name.startswith("KEY_KP"):
                        qt_mods = int(qt_mods) | int(_safe_int(QtCore.Qt.KeyboardModifier.KeypadModifier) or 0)
                except Exception:
                    pass

                self.key_event.emit(
                    KeyboardEvent(
                        source="evdev",
                        action=action,
                        text=f"evdev: {action} {key_name}",
                        qt_key=qt_key,
                        qt_modifiers=qt_mods,
                        evdev_code=_safe_int(event.code),
                        evdev_name=key_name,
                        device_name=dev_name,
                        timestamp=time.time(),
                    )
                )

                if action == "release":
                    _update_evdev_mods(self._active_mods, key_name, False)
        except Exception as e:
            self.status_changed.emit(KeyboardCaptureStatus.DEGRADED.value, f"evdev read loop ended: {e}")


def _update_evdev_mods(active_mods: set[str], key_name: str, pressed: bool) -> None:
    mod: str | None = None
    if key_name in ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"):
        mod = "shift"
    elif key_name in ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"):
        mod = "ctrl"
    elif key_name in ("KEY_LEFTALT", "KEY_RIGHTALT", "KEY_ALTGR"):
        mod = "alt"
    elif key_name in ("KEY_LEFTMETA", "KEY_RIGHTMETA", "KEY_SUPER"):
        mod = "meta"

    if not mod:
        return
    if pressed:
        active_mods.add(mod)
    else:
        active_mods.discard(mod)


def _evdev_name_to_qt_key(key_name: str) -> int | None:
    # Letters
    if key_name.startswith("KEY_") and len(key_name) == 5 and key_name[4].isalpha():
        base = int(QtCore.Qt.Key.Key_A)
        return base + (ord(key_name[4]) - ord("A"))

    # Digits
    if key_name.startswith("KEY_") and len(key_name) == 5 and key_name[4].isdigit():
        return int(QtCore.Qt.Key.Key_0) + int(key_name[4])

    # Numpad digits
    if key_name.startswith("KEY_KP") and len(key_name) == 7 and key_name[6].isdigit():
        return int(QtCore.Qt.Key.Key_0) + int(key_name[6])

    mapping: dict[str, int] = {
        "KEY_SPACE": int(QtCore.Qt.Key.Key_Space),
        "KEY_ENTER": int(QtCore.Qt.Key.Key_Return),
        "KEY_KPENTER": int(QtCore.Qt.Key.Key_Return),
        "KEY_ESC": int(QtCore.Qt.Key.Key_Escape),
        "KEY_TAB": int(QtCore.Qt.Key.Key_Tab),
        "KEY_BACKSPACE": int(QtCore.Qt.Key.Key_Backspace),
        "KEY_DELETE": int(QtCore.Qt.Key.Key_Delete),
        "KEY_UP": int(QtCore.Qt.Key.Key_Up),
        "KEY_DOWN": int(QtCore.Qt.Key.Key_Down),
        "KEY_LEFT": int(QtCore.Qt.Key.Key_Left),
        "KEY_RIGHT": int(QtCore.Qt.Key.Key_Right),
        "KEY_MINUS": int(QtCore.Qt.Key.Key_Minus),
        "KEY_EQUAL": int(QtCore.Qt.Key.Key_Equal),
        "KEY_SLASH": int(QtCore.Qt.Key.Key_Slash),
        "KEY_BACKSLASH": int(QtCore.Qt.Key.Key_Backslash),
        # Modifiers
        "KEY_LEFTSHIFT": int(QtCore.Qt.Key.Key_Shift),
        "KEY_RIGHTSHIFT": int(QtCore.Qt.Key.Key_Shift),
        "KEY_LEFTCTRL": int(QtCore.Qt.Key.Key_Control),
        "KEY_RIGHTCTRL": int(QtCore.Qt.Key.Key_Control),
        "KEY_LEFTALT": int(QtCore.Qt.Key.Key_Alt),
        "KEY_RIGHTALT": int(QtCore.Qt.Key.Key_Alt),
        "KEY_LEFTMETA": int(QtCore.Qt.Key.Key_Meta),
        "KEY_RIGHTMETA": int(QtCore.Qt.Key.Key_Meta),
    }
    return mapping.get(key_name)
