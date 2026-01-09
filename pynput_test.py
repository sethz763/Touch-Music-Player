"""Compare global keyboard capture (pynput) vs Qt key events.

Left side: pynput (global) should update even when this window is not focused.
Right side: Qt key events update only when this window/widget has focus.

Linux support notes (X11 + Wayland)
----------------------------------

Why this is special on Linux:
- Under X11 (Xorg), user-space apps can typically register global key hooks.
- Under Wayland, global keylogging is intentionally restricted by the compositor
    for security reasons. Libraries like pynput may fail or only work in limited
    scenarios (sometimes via XWayland, sometimes not at all).

Strategy used by this test app:
- Windows/macOS/Linux-X11: use pynput for global capture.
- Linux-Wayland: prefer evdev (reads kernel input events) when available.

What "good" looks like on Linux:
- With evdev, numpad keys should show as KEY_KP0..KEY_KP9, KEY_KPENTER, etc.
    (unambiguous).
- With pynput, numpad vs top-row digits may be ambiguous if you only look at
    key.char; that's why this app prints vk/sc when present.

Wayland + evdev requirements:
1) Install dependency:
     - pip install evdev
     (This is Linux-only; Windows/macOS ignore it.)

2) Permissions to read /dev/input/event*:
     evdev reads from /dev/input devices. Many distros restrict these devices to
     root or to a privileged group (commonly "input"). If you see a message like
     "no readable keyboard devices" or permission errors, you need to grant read
     access.

     Common approaches (distro-dependent):
     - Add your user to the input group (if your distro uses it):
             sudo usermod -aG input $USER
         Then log out and back in.

     - Or add a udev rule to grant access to input devices.
         Example (review carefully for your security needs):
             /etc/udev/rules.d/99-evdev-input.rules
             SUBSYSTEM=="input", KERNEL=="event*", GROUP="input", MODE="0660"
         Then reload rules / reboot.

Security note:
- Reading /dev/input provides low-level access to keyboard events for the whole
    system. Treat this like a privileged capability. Prefer the least-permissive
    rule that still meets your requirements.

If you truly need global capture on Wayland without evdev:
- There is no universal, unprivileged cross-desktop API that works like a
    keylogger. Solutions are compositor/desktop specific (portals/protocols) and
    are not generally intended for arbitrary global hotkey capture.

Run:
    venv\\Scripts\\python.exe pynput_test.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass

from pynput import keyboard
from PySide6 import QtCore, QtGui, QtWidgets


_PRINT_LOCK = threading.Lock()


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except TypeError:
        try:
            return int(getattr(value, "value"))
        except Exception:
            return None
    except Exception:
        return None


def _native_attr_int(obj: object, name: str) -> int | None:
    """Fetch Qt native* attr as int (callable-or-int)."""
    try:
        attr = getattr(obj, name, None)
        if attr is None:
            return None
        return int(attr() if callable(attr) else attr)
    except Exception:
        return None


def _print_kv(prefix: str, fields: dict[str, object]) -> None:
    ts = time.time()
    parts = [f"{prefix}", f"t={ts:.3f}"]
    for k, v in fields.items():
        parts.append(f"{k}={v}")
    line = " ".join(parts)
    with _PRINT_LOCK:
        print(line, flush=True)


@dataclass(frozen=True)
class KeyEventView:
    source: str
    action: str
    key: str

    def to_text(self) -> str:
        return f"{self.source}: {self.action} {self.key}"


def _format_pynput_key(key: keyboard.Key | keyboard.KeyCode) -> str:
    if isinstance(key, keyboard.KeyCode):
        parts: list[str] = []

        # For many keys (including some numpad cases), char can be None or
        # ambiguous. On Windows, vk is typically the most useful identifier.
        char = getattr(key, "char", None)
        if char is not None:
            parts.append(f"char={repr(char)}")

        vk = getattr(key, "vk", None)
        if vk is not None:
            try:
                vk_int = int(vk)
            except TypeError:
                vk_int = int(getattr(vk, "value"))
            parts.append(f"vk=0x{vk_int:02X}({vk_int})")

        scan_code = getattr(key, "scan_code", None)
        if scan_code is not None:
            try:
                sc_int = int(scan_code)
            except TypeError:
                sc_int = int(getattr(scan_code, "value"))
            parts.append(f"sc={sc_int}")

        if not parts:
            return repr(key)
        return " ".join(parts)
    # keyboard.Key
    return str(key).replace("Key.", "")


def _log_pynput_event(action: str, key: keyboard.Key | keyboard.KeyCode) -> None:
    fields: dict[str, object] = {
        "action": action,
        "type": type(key).__name__,
    }
    try:
        fields["repr"] = repr(key)
    except Exception:
        pass

    if isinstance(key, keyboard.KeyCode):
        fields["char"] = getattr(key, "char", None)
        fields["vk"] = _safe_int(getattr(key, "vk", None))
        fields["scan_code"] = _safe_int(getattr(key, "scan_code", None))
    else:
        # keyboard.Key
        try:
            fields["name"] = str(key).replace("Key.", "")
        except Exception:
            pass

    _print_kv("PYNPUT", fields)


def _format_qt_key(event: QtGui.QKeyEvent) -> str:
    # Prefer a readable shortcut-style representation (e.g. Ctrl+A).
    mods = event.modifiers()
    key = event.key()
    try:
        mods_int = int(mods)
    except TypeError:
        mods_int = int(getattr(mods, "value"))
    try:
        key_int = int(key)
    except TypeError:
        key_int = int(getattr(key, "value"))

    seq = QtGui.QKeySequence(mods_int | key_int)
    text = seq.toString(QtGui.QKeySequence.SequenceFormat.NativeText)
    if not text:
        text = event.text() or f"Key({int(event.key())})"
    if event.isAutoRepeat():
        text += " (auto-repeat)"
    return text


def _log_qt_event(action: str, event: QtGui.QKeyEvent) -> None:
    key_int = _safe_int(event.key()) or 0
    mods_int = _safe_int(event.modifiers()) or 0
    combo = int(mods_int | key_int)
    try:
        seq = QtGui.QKeySequence(combo).toString(QtGui.QKeySequence.SequenceFormat.NativeText)
    except Exception:
        seq = ""

    fields: dict[str, object] = {
        "action": action,
        "key": int(key_int),
        "mods": int(mods_int),
        "combo": int(combo),
        "seq": repr(seq),
        "text": repr(event.text() or ""),
        "auto": bool(getattr(event, "isAutoRepeat", lambda: False)()),
        "vk": _native_attr_int(event, "nativeVirtualKey"),
        "sc": _native_attr_int(event, "nativeScanCode"),
        "nmods": _native_attr_int(event, "nativeModifiers"),
    }

    # Handy flags for numpad debugging.
    try:
        keypad_mask = _safe_int(QtCore.Qt.KeyboardModifier.KeypadModifier) or 0
        fields["keypadMod"] = bool(int(mods_int) & int(keypad_mask))
    except Exception:
        fields["keypadMod"] = False

    # Some Qt builds expose dedicated numpad keys.
    try:
        for d in range(10):
            for attr in (f"Key_Numpad{d}", f"Keypad{d}"):
                v = getattr(QtCore.Qt.Key, attr, None)
                if v is not None and (_safe_int(v) or 0) == int(key_int):
                    fields["numpadKey"] = attr
                    fields["numpadDigit"] = d
                    raise StopIteration
    except StopIteration:
        pass
    except Exception:
        pass

    _print_kv("QT", fields)


class _PynputBridge(QtCore.QObject):
    event_text = QtCore.Signal(str)


class QtKeyCaptureWidget(QtWidgets.QFrame):
    key_event_text = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        try:
            _log_qt_event("press", event)
        except Exception:
            pass
        self.key_event_text.emit(f"Qt: press  {_format_qt_key(event)}")
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QtGui.QKeyEvent) -> None:
        try:
            _log_qt_event("release", event)
        except Exception:
            pass
        self.key_event_text.emit(f"Qt: release {_format_qt_key(event)}")
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        # Convenience: click to give keyboard focus.
        self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)


class KeyCompareWindow(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Keyboard capture: pynput (global) vs Qt (focus)")
        self.resize(900, 240)

        self._pynput_bridge = _PynputBridge()
        self._pynput_bridge.event_text.connect(self._set_pynput_text)

        self._global_listener: object | None = None

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(16)

        self._pynput_panel, self._pynput_value = self._build_panel(
            title="Global keyboard (pynput/evdev)",
            subtitle="Updates even if window not focused",
        )
        root.addWidget(self._pynput_panel, 1)

        self._qt_panel, self._qt_value, self._qt_capture = self._build_qt_panel()
        root.addWidget(self._qt_panel, 1)

        self._start_global_listener()

        # Put focus on the Qt capture area so right side works immediately.
        QtCore.QTimer.singleShot(0, self._qt_capture.setFocus)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        listener = self._global_listener
        self._global_listener = None
        if listener is not None and hasattr(listener, "stop"):
            try:
                listener.stop()  # type: ignore[no-untyped-call]
            except Exception:
                pass
        super().closeEvent(event)

    def _build_panel(
        self,
        *,
        title: str,
        subtitle: str,
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
        panel = QtWidgets.QFrame(self)
        panel.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)

        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title_label = QtWidgets.QLabel(title, panel)
        title_label.setWordWrap(True)
        title_font = QtGui.QFont()
        title_font.setPointSize(title_font.pointSize() + 2)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        subtitle_label = QtWidgets.QLabel(subtitle, panel)
        subtitle_label.setWordWrap(True)
        subtitle_label.setStyleSheet("color: gray;")
        layout.addWidget(subtitle_label)

        value = QtWidgets.QLabel("(waiting for input)", panel)
        value.setWordWrap(True)
        value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        value_font = QtGui.QFont("Consolas")
        value_font.setPointSize(value_font.pointSize() + 1)
        value.setFont(value_font)
        layout.addWidget(value)

        layout.addStretch(1)
        return panel, value

    def _build_qt_panel(
        self,
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel, QtKeyCaptureWidget]:
        panel = QtWidgets.QFrame(self)
        panel.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)

        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title_label = QtWidgets.QLabel("Qt (focused key events)", panel)
        title_label.setWordWrap(True)
        title_font = QtGui.QFont()
        title_font.setPointSize(title_font.pointSize() + 2)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        subtitle_label = QtWidgets.QLabel(
            "Only updates while this window has focus (click this panel)",
            panel,
        )
        subtitle_label.setWordWrap(True)
        subtitle_label.setStyleSheet("color: gray;")
        layout.addWidget(subtitle_label)

        capture = QtKeyCaptureWidget(panel)
        cap_layout = QtWidgets.QVBoxLayout(capture)
        cap_layout.setContentsMargins(8, 8, 8, 8)
        cap_layout.setSpacing(6)

        value = QtWidgets.QLabel("(click here, then type)", capture)
        value.setWordWrap(True)
        value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        value_font = QtGui.QFont("Consolas")
        value_font.setPointSize(value_font.pointSize() + 1)
        value.setFont(value_font)
        cap_layout.addWidget(value)

        capture.key_event_text.connect(value.setText)
        layout.addWidget(capture, 1)
        return panel, value, capture

    def _set_pynput_text(self, text: str) -> None:
        self._pynput_value.setText(text)

    def _start_global_listener(self) -> None:
        is_linux = sys.platform.startswith("linux")
        is_wayland = is_linux and bool(os.environ.get("WAYLAND_DISPLAY"))

        # On Linux Wayland, global key capture via pynput is often restricted.
        # Prefer evdev if available.
        if is_wayland:
            evdev_listener = _try_start_evdev_listener(self._pynput_bridge)
            if evdev_listener is not None:
                self._global_listener = evdev_listener
                self._pynput_value.setText(
                    "evdev: (waiting for input)  [Wayland global capture]"
                )
                return
            self._pynput_value.setText(
                "Wayland detected; evdev not available/authorized. "
                "Global capture may not work until evdev is installed and has permissions."
            )

        def on_press(key: keyboard.Key | keyboard.KeyCode) -> None:
            try:
                _log_pynput_event("press", key)
            except Exception:
                pass
            ev = KeyEventView(source="pynput", action="press", key=_format_pynput_key(key))
            self._pynput_bridge.event_text.emit(ev.to_text())

        def on_release(key: keyboard.Key | keyboard.KeyCode) -> None:
            try:
                _log_pynput_event("release", key)
            except Exception:
                pass
            ev = KeyEventView(source="pynput", action="release", key=_format_pynput_key(key))
            self._pynput_bridge.event_text.emit(ev.to_text())

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        self._global_listener = listener


def _try_start_evdev_listener(bridge: _PynputBridge) -> object | None:
    """Try to start an evdev-based global keyboard listener (Linux).

    This works under both X11 and Wayland, but often requires permissions to read
    from /dev/input/event*.
    """

    try:
        import evdev  # type: ignore
        from evdev import ecodes  # type: ignore
    except Exception:
        return None

    class _EvdevListener:
        def __init__(self) -> None:
            self._stop = threading.Event()
            self._threads: list[threading.Thread] = []
            self._devices: list[object] = []

        def start(self) -> None:
            paths = []
            try:
                paths = list(evdev.list_devices())
            except Exception as e:
                bridge.event_text.emit(f"evdev: init failed ({type(e).__name__}: {e})")
                return

            opened_any = False

            for path in paths:
                if self._stop.is_set():
                    break

                try:
                    dev = evdev.InputDevice(path)
                    caps = dev.capabilities(verbose=False)
                    if ecodes.EV_KEY not in caps:
                        try:
                            dev.close()
                        except Exception:
                            pass
                        continue
                except Exception:
                    # Commonly permission denied; ignore and continue.
                    continue

                opened_any = True
                self._devices.append(dev)

                t = threading.Thread(
                    target=self._read_loop,
                    args=(dev,),
                    name=f"evdev-{getattr(dev, 'name', 'kbd')}",
                    daemon=True,
                )
                self._threads.append(t)
                t.start()

            if not opened_any:
                bridge.event_text.emit(
                    "evdev: no readable keyboard devices. "
                    "On Linux you may need to run as root or add your user to the 'input' group / udev rules."
                )
            else:
                bridge.event_text.emit("evdev: listening (numpad keys should appear as KEY_KP*)")

        def stop(self) -> None:
            self._stop.set()
            for dev in list(self._devices):
                try:
                    dev.close()
                except Exception:
                    pass

        def _read_loop(self, dev: object) -> None:
            try:
                for event in dev.read_loop():  # type: ignore[attr-defined]
                    if self._stop.is_set():
                        break
                    if event.type != ecodes.EV_KEY:
                        continue

                    # 0=up, 1=down, 2=hold
                    if event.value == 1:
                        action = "press"
                    elif event.value == 0:
                        action = "release"
                    else:
                        action = "hold"

                    key_name = ecodes.KEY.get(event.code, f"KEY_{event.code}")
                    dev_name = getattr(dev, "name", "evdev")

                    try:
                        _print_kv(
                            "EVDEV",
                            {
                                "action": action,
                                "name": key_name,
                                "code": int(event.code),
                                "value": int(event.value),
                                "dev": repr(dev_name),
                            },
                        )
                    except Exception:
                        pass
                    bridge.event_text.emit(
                        f"evdev: {action} {key_name} code={event.code} dev={dev_name}"
                    )
            except Exception as e:
                bridge.event_text.emit(f"evdev: read loop ended ({type(e).__name__}: {e})")

    listener = _EvdevListener()
    listener.start()
    return listener


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    w = KeyCompareWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())