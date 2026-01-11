#settings
from __future__ import annotations

import sys
import multiprocessing as mp
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget, QHBoxLayout, QSpacerItem, QRadioButton, QSlider, QLabel, QComboBox, QMainWindow, QLineEdit, QSpinBox, QMessageBox, QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QDialog, QListWidget, QListWidgetItem
from PySide6.QtGui import QFont, QKeySequence
from PySide6 import QtCore
from PySide6 import QtWidgets
from PySide6.QtCore import QThread

from PySide6.QtMultimedia import QMediaDevices

from PySide6.QtCore import QObject, Signal, Qt, QSettings

from persistence.SaveSettings import SaveSettings

from gui.engine_adapter import EngineAdapter

import sounddevice as sd

from inspect import currentframe, getframeinfo


def _probe_streamdeck_devices_subprocess(out_q) -> None:
    """Enumerate StreamDeck devices in a separate process.

    Rationale: The underlying HID stack (hidapi/libusb) can crash the process
    during repeated open/close operations. Running probing in a subprocess
    isolates the GUI from native crashes.
    """
    import hashlib

    def _safe_call(fn, default=None):
        try:
            if callable(fn):
                return fn()
            return fn
        except Exception:
            return default

    try:
        repo_root = Path(__file__).resolve().parents[2]
        sp = repo_root / "venv" / "Lib" / "site-packages"
        if sp.exists() and sp.is_dir():
            sys.path.insert(0, str(sp))
    except Exception:
        pass

    try:
        from StreamDeck.DeviceManager import DeviceManager
    except Exception as e:
        try:
            out_q.put_nowait(([], False, f"StreamDeck import failed: {type(e).__name__}: {e}"))
        except Exception:
            pass
        return

    devices_out: list[dict] = []
    try:
        decks = DeviceManager().enumerate() or []
    except Exception as e:
        try:
            out_q.put_nowait(([], False, f"DeviceManager.enumerate failed: {type(e).__name__}: {e}"))
        except Exception:
            pass
        return

    for idx, deck in enumerate(decks):
        try:
            deck_type = _safe_call(getattr(deck, "deck_type", None), "Unknown")
            hwid = _safe_call(getattr(deck, "id", None), None)
            if not hwid:
                hwid = f"deck_{idx}"
            hwid = str(hwid)

            available = False
            busy_reason = ""
            serial_number = ""

            is_open = bool(_safe_call(getattr(deck, "is_open", None), False))
            if is_open:
                available = False
                busy_reason = "in use (opened)"
            else:
                try:
                    deck.open()
                    available = True
                    try:
                        serial_number = str(_safe_call(getattr(deck, "get_serial_number", None), "") or "")
                    except Exception:
                        serial_number = ""
                except Exception as open_exc:
                    available = False
                    busy_reason = f"unavailable ({type(open_exc).__name__})"
                finally:
                    try:
                        if bool(_safe_call(getattr(deck, "is_open", None), False)):
                            deck.close()
                    except Exception:
                        pass

            serial_number = str(serial_number or "").strip()
            if serial_number:
                device_key = serial_number
            else:
                digest = hashlib.sha1(hwid.encode("utf-8", errors="ignore")).hexdigest()[:12]
                device_key = f"hid_{digest}"
                if not serial_number:
                    serial_number = "Unknown"

            devices_out.append(
                {
                    "id": str(device_key),
                    "type": str(deck_type),
                    "serial": str(serial_number),
                    "hwid": str(hwid),
                    "available": bool(available),
                    "busy_reason": str(busy_reason),
                }
            )
        except Exception:
            continue

    try:
        out_q.put_nowait((devices_out, True, ""))
    except Exception:
        pass


class _StreamdeckRefreshWorker(QObject):
    """Background worker to enumerate StreamDeck devices without blocking the UI."""

    done = Signal(list, bool, str)  # devices, ok, err

    def __init__(self):
        super().__init__()

    @QtCore.Slot()
    def run(self) -> None:
        # Run probing in a subprocess so native HID crashes don't take down the UI.
        try:
            ctx = mp.get_context("spawn")
            out_q = ctx.Queue()
            proc = ctx.Process(target=_probe_streamdeck_devices_subprocess, args=(out_q,), daemon=True)
            proc.start()
        except Exception as e:
            self.done.emit([], False, f"StreamDeck probe spawn failed: {type(e).__name__}: {e}")
            return

        try:
            devices_out, ok, err = out_q.get(timeout=4.0)
        except Exception as e:
            try:
                if proc.is_alive():
                    proc.terminate()
            except Exception:
                pass
            self.done.emit([], False, f"StreamDeck probe timeout: {type(e).__name__}: {e}")
            return
        finally:
            try:
                proc.join(timeout=0.5)
            except Exception:
                pass

        try:
            self.done.emit(list(devices_out or []), bool(ok), str(err or ""))
        except Exception:
            self.done.emit([], False, "StreamDeck probe failed")


class _DeviceRefreshWorker(QObject):
    """Background worker to enumerate audio devices without blocking the UI."""

    done = Signal(list, tuple, bool, str)  # devices, apis, ok, err

    def __init__(self):
        super().__init__()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            try:
                devices = sd.query_devices()
            except Exception:
                devices = []
            try:
                apis = sd.query_hostapis()
            except Exception:
                apis = ()

            self.done.emit(devices, apis, True, "")
        except Exception as e:
            self.done.emit([], (), False, f"{type(e).__name__}: {e}")


class SettingSignals(QObject):
    change_rows_and_columns_signal = Signal(int, int)
    main_output_signal = Signal(int, float)
    editor_output_signal = Signal(int, float)


class KeyboardShortcutsTab(QWidget):
    """Tab for displaying/editing keyboard shortcut mappings."""

    shortcuts_changed = Signal(list)
    global_capture_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        layout = QVBoxLayout()
        self.setLayout(layout)

        title = QLabel('Keyboard Shortcuts')
        layout.addWidget(title)

        # Global keyboard capture toggle (wired by MainWindow).
        self.global_capture_checkbox = QCheckBox('Global keyboard capture')
        self.global_capture_help = QLabel('Captures keyboard when app is not in focus')
        self.global_capture_help.setStyleSheet('color: gray;')
        layout.addWidget(self.global_capture_checkbox)
        layout.addWidget(self.global_capture_help)

        try:
            self._load_persisted_global_capture()
        except Exception:
            pass
        self.global_capture_checkbox.toggled.connect(self._on_global_capture_toggled)

        # Action categories are UI-only for now; more actions can be added later.
        # Keep stable/canonical action names because we persist by action string.
        self._action_aliases: dict[str, str] = {
            # Legacy: fade was previously a hold/modifier action.
            'Trigger fade': 'Fade all active cues',
        }
        self.action_categories: dict[str, list[str]] = {
            'Playback': [
                'Fade all active cues',
                'Transport Play',
                'Transport Pause',
                'Transport Stop',
                'Next cue',
            ],
            'Streamdeck': [
                'Select bank 0 on streamdeck',
                'Select bank 1 on streamdeck',
                'Select bank 2 on streamdeck',
                'Select bank 3 on streamdeck',
                'Select bank 4 on streamdeck',
                'Select bank 5 on streamdeck',
                'Select bank 6 on streamdeck',
                'Select bank 7 on streamdeck',
                'Select bank 8 on streamdeck',
                'Select bank 9 on streamdeck',
            ],
        }

        self.shortcuts_table = QTableWidget()
        self.shortcuts_table.setColumnCount(2)
        self.shortcuts_table.setHorizontalHeaderLabels([
            'Modifier / Combo',
            'Action / Description',
        ])
        self.shortcuts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.shortcuts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.shortcuts_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.shortcuts_table.setAlternatingRowColors(True)
        self.shortcuts_table.verticalHeader().setVisible(False)

        header = self.shortcuts_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.shortcuts_table)

        # Clicking the Action column opens a picker dialog.
        self.shortcuts_table.cellClicked.connect(self._on_table_cell_clicked)

        self._load_example_rows()
        self._load_persisted_bindings()
        self._emit_bindings_changed()

    def _qsettings(self) -> QSettings:
        # Keep these stable so shortcuts persist across launches.
        return QSettings('StepD', 'TouchMusicPlayer')

    def _load_persisted_global_capture(self) -> None:
        try:
            s = self._qsettings()
            s.beginGroup('KeyboardCapture')
            enabled = s.value('global_enabled', False)
            s.endGroup()
            self.global_capture_checkbox.setChecked(bool(enabled))
        except Exception:
            self.global_capture_checkbox.setChecked(False)

    def _save_persisted_global_capture(self, enabled: bool) -> None:
        try:
            s = self._qsettings()
            s.beginGroup('KeyboardCapture')
            s.setValue('global_enabled', bool(enabled))
            s.endGroup()
        except Exception:
            pass

    def _on_global_capture_toggled(self, enabled: bool) -> None:
        try:
            self._save_persisted_global_capture(bool(enabled))
        except Exception:
            pass
        try:
            self.global_capture_toggled.emit(bool(enabled))
        except Exception:
            pass

    def _normalize_key_value(self, key_val: int | None) -> int | None:
        """Return a stable integer for a Qt key combo.

        Note: Do NOT strip `KeypadModifier`. We want to allow distinct bindings
        for numpad digits vs top-row digits.
        """
        if key_val is None:
            return None
        try:
            return int(key_val)
        except Exception:
            return None

    def _format_key_value(self, key_val: int) -> str:
        """Human-readable key combo string with keypad hint."""
        try:
            v = int(key_val)
        except Exception:
            return ''

        try:
            mods = int(v) & _qt_int(Qt.KeyboardModifier.KeyboardModifierMask)
        except Exception:
            mods = 0

        try:
            key = int(v) & ~_qt_int(Qt.KeyboardModifier.KeyboardModifierMask)
        except Exception:
            key = int(v)

        is_keypad = False
        try:
            is_keypad = bool(int(v) & _qt_int(Qt.KeyboardModifier.KeypadModifier))
        except Exception:
            is_keypad = False

        # If it's a digit and KeypadModifier is present, show "Numpad N".
        try:
            if is_keypad and int(Qt.Key.Key_0) <= int(key) <= int(Qt.Key.Key_9):
                digit = int(key) - int(Qt.Key.Key_0)
                prefix = QKeySequence(int(mods)).toString().strip()
                if prefix:
                    return f"{prefix}+Numpad {digit}"
                return f"Numpad {digit}"
        except Exception:
            pass

        try:
            return QKeySequence(int(v)).toString()
        except Exception:
            return ''

    def _load_example_rows(self) -> None:
        """Seed rows with available actions.

        Keys are intentionally left blank/unassigned by default.
        """
        actions: list[str] = []
        for cat in self.action_categories.values():
            actions.extend(list(cat or []))

        self.shortcuts_table.setRowCount(len(actions))
        for r, action in enumerate(actions):
            combo_item = QTableWidgetItem('')
            combo_item.setData(Qt.ItemDataRole.UserRole, None)
            combo_item.setToolTip('Click to set key / key combo')
            action_item = QTableWidgetItem(action)
            action_item.setToolTip('Click to choose an action')
            self.shortcuts_table.setItem(r, 0, combo_item)
            self.shortcuts_table.setItem(r, 1, action_item)

        # Persistence/load happens after table is seeded.

    def _get_table_rows_for_persistence(self) -> list[dict]:
        rows: list[dict] = []
        for row in range(self.shortcuts_table.rowCount()):
            combo_item = self.shortcuts_table.item(row, 0)
            action_item = self.shortcuts_table.item(row, 1)
            if action_item is None:
                continue
            action_text = (action_item.text() or '').strip()
            if not action_text:
                continue
            key_val = combo_item.data(Qt.ItemDataRole.UserRole) if combo_item is not None else None
            try:
                key_val = int(key_val)
            except Exception:
                key_val = -1
            rows.append({'action': action_text, 'key': int(key_val) if key_val >= 0 else -1})
        return rows

    def _save_persisted_bindings(self) -> None:
        try:
            s = self._qsettings()
            s.beginGroup('KeyboardShortcuts')
            # Clear group so removed/renamed rows don't linger.
            s.remove('')

            rows = self._get_table_rows_for_persistence()
            s.beginWriteArray('bindings', len(rows))
            for i, r in enumerate(rows):
                s.setArrayIndex(i)
                s.setValue('action', r.get('action', ''))
                s.setValue('key', int(r.get('key', -1)))
            s.endArray()
            s.endGroup()
        except Exception:
            pass

    def _load_persisted_bindings(self) -> None:
        action_to_key: dict[str, int] = {}
        try:
            s = self._qsettings()
            s.beginGroup('KeyboardShortcuts')
            size = s.beginReadArray('bindings')
            for i in range(size):
                s.setArrayIndex(i)
                try:
                    action = str(s.value('action', '') or '').strip()
                except Exception:
                    action = ''
                # Migrate legacy names to the current canonical action.
                try:
                    if action:
                        action = self._action_aliases.get(action, action)
                except Exception:
                    pass
                try:
                    key_val = int(s.value('key', -1))
                except Exception:
                    key_val = -1
                if action:
                    action_to_key[action] = key_val
            s.endArray()
            s.endGroup()
        except Exception:
            action_to_key = {}

        for row in range(self.shortcuts_table.rowCount()):
            combo_item = self.shortcuts_table.item(row, 0)
            action_item = self.shortcuts_table.item(row, 1)
            if action_item is None:
                continue
            action_text = (action_item.text() or '').strip()
            key_val = action_to_key.get(action_text, -1)

            if combo_item is None:
                combo_item = QTableWidgetItem('')
                self.shortcuts_table.setItem(row, 0, combo_item)

            if isinstance(key_val, int) and key_val >= 0:
                combo_item.setData(Qt.ItemDataRole.UserRole, int(key_val))
                combo_item.setText(self._format_key_value(int(key_val)))
            else:
                combo_item.setData(Qt.ItemDataRole.UserRole, None)
                combo_item.setText('')

    def get_bindings(self) -> list[dict]:
        """Return current bindings as a list of dicts (UI-only; no persistence)."""
        bindings: list[dict] = []
        for row in range(self.shortcuts_table.rowCount()):
            combo_item = self.shortcuts_table.item(row, 0)
            action_item = self.shortcuts_table.item(row, 1)
            if combo_item is None or action_item is None:
                continue

            key_val = combo_item.data(Qt.ItemDataRole.UserRole)
            try:
                key_val = int(key_val)
            except Exception:
                key_val = None

            action_text = action_item.text() if action_item is not None else ''
            if key_val is None or not action_text:
                continue
            bindings.append({
                'key': key_val,
                'action': action_text,
            })
        return bindings

    def _emit_bindings_changed(self) -> None:
        try:
            self._save_persisted_bindings()
        except Exception:
            pass
        try:
            self.shortcuts_changed.emit(self.get_bindings())
        except Exception:
            pass

    def _on_table_cell_clicked(self, row: int, column: int) -> None:
        """Handle clicks for combo capture and action selection."""

        combo_col = 0
        action_col = 1

        if column == combo_col:
            dlg = _KeyCaptureDialog(parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                key_val = dlg.get_key_value()
                item = self.shortcuts_table.item(row, combo_col)
                if item is None:
                    item = QTableWidgetItem('')
                    self.shortcuts_table.setItem(row, combo_col, item)

                # key_val == None means "clear".
                if key_val is None:
                    item.setData(Qt.ItemDataRole.UserRole, None)
                    item.setText('')
                    self._emit_bindings_changed()
                    return

                item.setData(Qt.ItemDataRole.UserRole, int(key_val))
                item.setText(self._format_key_value(int(key_val)))
                self._emit_bindings_changed()
            return

        if column != action_col:
            return

        current_item = self.shortcuts_table.item(row, action_col)
        current_action = current_item.text() if current_item is not None else ''

        dlg = _ActionPickerDialog(self.action_categories, current_action=current_action, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            chosen = dlg.get_selected_action()
            if chosen:
                if current_item is None:
                    current_item = QTableWidgetItem('')
                    self.shortcuts_table.setItem(row, action_col, current_item)
                current_item.setText(chosen)
                self._emit_bindings_changed()


def _qt_int(value, default: int = 0) -> int:
    """Best-effort int conversion for PySide6 Qt enums/flags.

    PySide6 Qt flag/enums sometimes don't support `int(x)` directly but expose a
    `.value` attribute.
    """
    try:
        return int(value)
    except TypeError:
        try:
            return int(getattr(value, "value"))
        except Exception:
            return int(default)
    except Exception:
        return int(default)


class _KeyCaptureDialog(QDialog):
    """Modal dialog that captures a key or key-combo from user input."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle('Press Key Combo')
        self.setModal(True)

        self._key_value: int | None = None
        self._captured_key: int | None = None

        layout = QVBoxLayout()
        self.setLayout(layout)

        self._label = QLabel('Press the desired key or key combo now...\n(Esc = cancel, Backspace/Delete = clear)')
        layout.addWidget(self._label)

        # Make sure we get key events.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._label.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def keyPressEvent(self, event):
        try:
            if getattr(event, 'isAutoRepeat', None) and event.isAutoRepeat():
                return
        except Exception:
            pass

        key = _qt_int(getattr(event, "key", lambda: 0)())

        mods = _qt_int(getattr(event, "modifiers", lambda: 0)())

        # Canonicalize Qt's numpad digit variants into "digit + KeypadModifier".
        try:
            numpad_digit = None
            for d in range(10):
                for attr in (f"Key_Numpad{d}", f"Keypad{d}"):
                    v = getattr(Qt.Key, attr, None)
                    if v is not None and _qt_int(v) == int(key):
                        numpad_digit = int(d)
                        break
                if numpad_digit is not None:
                    break
            if numpad_digit is not None:
                key = _qt_int(Qt.Key.Key_0) + int(numpad_digit)
                mods = int(mods) | _qt_int(Qt.KeyboardModifier.KeypadModifier)
        except Exception:
            pass

        # Cancel
        if key == int(Qt.Key.Key_Escape):
            self.reject()
            return

        # Clear
        if key in (int(Qt.Key.Key_Backspace), int(Qt.Key.Key_Delete)):
            self._key_value = None
            self._captured_key = None
            try:
                self._label.setText('Cleared (no key assigned).')
            except Exception:
                pass
            self.accept()
            return

        # Ignore modifier-only presses (wait for a real key).
        if key in (
            _qt_int(Qt.Key.Key_Control),
            _qt_int(Qt.Key.Key_Shift),
            _qt_int(Qt.Key.Key_Alt),
            _qt_int(Qt.Key.Key_Meta),
        ):
            try:
                mods_preview = int(mods)
                if mods_preview:
                    self._label.setText(
                        f"Holding: {QKeySequence(int(mods_preview)).toString()}\n"
                        "Now press a key to complete the combo...\n(Esc = cancel)"
                    )
            except Exception:
                pass
            return

        # Best-effort: add KeypadModifier for numpad keys.
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
            if 0x60 <= int(vk) <= 0x6F:  # Windows keypad VK range
                mods |= _qt_int(Qt.KeyboardModifier.KeypadModifier)
            if int(vk) in {82, 83, 84, 85, 86, 87, 88, 89, 91, 92}:  # mac keypad digits
                mods |= _qt_int(Qt.KeyboardModifier.KeypadModifier)
            # Linux (X11/Wayland): keypad keysyms tend to be 0xFFB0..0xFFBF.
            if 0xFFB0 <= int(vk) <= 0xFFBF:
                mods |= _qt_int(Qt.KeyboardModifier.KeypadModifier)
            # Windows: scan codes for numpad digits (Set 1 scancodes).
            # Top-row digits are 0x02..0x0B, whereas numpad digits are 0x47..0x52.
            if int(sc) in {0x47, 0x48, 0x49, 0x4B, 0x4C, 0x4D, 0x4F, 0x50, 0x51, 0x52}:
                mods |= _qt_int(Qt.KeyboardModifier.KeypadModifier)
        except Exception:
            pass

        self._captured_key = key
        self._key_value = mods | key
        try:
            # Try to show keypad distinction when possible.
            try:
                v = int(self._key_value)
                keypad_mask = _qt_int(Qt.KeyboardModifier.KeypadModifier)
                mod_mask = _qt_int(Qt.KeyboardModifier.KeyboardModifierMask)
                is_keypad = bool(v & int(keypad_mask))
                key_only = v & ~int(mod_mask)
                mods_only = v & int(mod_mask)
                if is_keypad and _qt_int(Qt.Key.Key_0) <= int(key_only) <= _qt_int(Qt.Key.Key_9):
                    digit = int(key_only) - _qt_int(Qt.Key.Key_0)
                    prefix = QKeySequence(int(mods_only)).toString().strip()
                    if prefix:
                        pretty = f"{prefix}+Numpad {digit}"
                    else:
                        pretty = f"Numpad {digit}"
                else:
                    pretty = QKeySequence(int(v)).toString()
            except Exception:
                pretty = QKeySequence(int(self._key_value)).toString()

            # Include raw native identifiers so we can debug keypad detection on
            # platforms where Qt doesn't set KeypadModifier.
            self._label.setText(
                f"Captured: {pretty}\n"
                f"raw: key={int(key)} mods=0x{int(mods):X} vk={int(vk)} sc={int(sc)}\n"
                "(Press another combo to replace, or Esc to cancel)"
            )
        except Exception:
            pass

    def keyReleaseEvent(self, event):
        """Accept once the captured non-modifier key is released.

        This improves reliability for modifier combos on some platforms.
        """
        key = _qt_int(getattr(event, "key", lambda: 0)())
        if self._key_value is None or self._captured_key is None:
            return
        if key != int(self._captured_key):
            return
        self.accept()

    def get_key_value(self) -> int | None:
        return self._key_value


class _ActionPickerDialog(QDialog):
    """Simple modal picker for choosing an action.

    UI-only: does not persist and does not wire actions to behavior.
    """

    def __init__(
        self,
        categories: dict[str, list[str]],
        current_action: str = '',
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle('Choose Action')
        self.setModal(True)

        self._categories = categories
        self._selected_action: str = ''

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.category_combo = QComboBox()
        self.category_combo.addItems(list(self._categories.keys()))
        layout.addWidget(QLabel('Category'))
        layout.addWidget(self.category_combo)

        self.action_list = QListWidget()
        layout.addWidget(QLabel('Actions'))
        layout.addWidget(self.action_list)

        button_row = QHBoxLayout()
        self.ok_button = QPushButton('OK')
        self.cancel_button = QPushButton('Cancel')
        button_row.addItem(QSpacerItem(10, 10, QtWidgets.QSizePolicy.Policy.Expanding))
        button_row.addWidget(self.ok_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        self.category_combo.currentIndexChanged.connect(self._reload_actions)
        self.action_list.itemDoubleClicked.connect(self._accept_from_item)
        self.ok_button.clicked.connect(self._accept_from_selection)
        self.cancel_button.clicked.connect(self.reject)

        # Initialize list + try to preselect the current action.
        self._preselect_current_action(current_action)

    def _reload_actions(self) -> None:
        self.action_list.clear()
        category = self.category_combo.currentText()
        for action in self._categories.get(category, []):
            self.action_list.addItem(QListWidgetItem(action))

    def _preselect_current_action(self, current_action: str) -> None:
        # Try to find which category contains the current action.
        chosen_category = None
        if current_action:
            for cat, actions in self._categories.items():
                if current_action in actions:
                    chosen_category = cat
                    break

        if chosen_category is not None:
            idx = self.category_combo.findText(chosen_category)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)

        self._reload_actions()

        if current_action:
            matches = self.action_list.findItems(current_action, Qt.MatchFlag.MatchExactly)
            if matches:
                self.action_list.setCurrentItem(matches[0])

    def _accept_from_item(self, item: QListWidgetItem) -> None:
        self._selected_action = item.text()
        self.accept()

    def _accept_from_selection(self) -> None:
        item = self.action_list.currentItem()
        if item is None:
            return
        self._selected_action = item.text()
        self.accept()

    def get_selected_action(self) -> str:
        return self._selected_action


class StreamdeckTab(QWidget):
    """Tab for configuring Streamdeck device support."""

    streamdeck_enabled_changed = Signal(bool)
    device_enabled_changed = Signal(str, bool)  # device_id, enabled
    streamdeck_refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        layout = QVBoxLayout()
        self.setLayout(layout)

        title = QLabel('Streamdeck Configuration')
        layout.addWidget(title)

        # Global Streamdeck enable/disable checkbox
        self.global_enable_checkbox = QCheckBox('Enable Streamdeck support')
        self.global_enable_help = QLabel('Enable or disable Streamdeck integration globally')
        self.global_enable_help.setStyleSheet('color: gray;')
        layout.addWidget(self.global_enable_checkbox)
        layout.addWidget(self.global_enable_help)

        try:
            self._load_persisted_global_enable()
        except Exception:
            pass
        self.global_enable_checkbox.toggled.connect(self._on_global_enable_toggled)

        # Device list section
        device_section_label = QLabel('Available Devices')
        device_section_label.setStyleSheet('font-weight: bold; margin-top: 10px;')
        layout.addWidget(device_section_label)

        self.device_list_widget = QWidget()
        self.device_list_layout = QVBoxLayout()
        self.device_list_widget.setLayout(self.device_list_layout)
        layout.addWidget(self.device_list_widget)

        # Refresh button
        self.refresh_button = QPushButton('Refresh Device List')
        self.refresh_button.clicked.connect(self._on_refresh_button_clicked)
        layout.addWidget(self.refresh_button)

        # Spacer to push everything to the top
        layout.addItem(QSpacerItem(10, 10, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding))

        # Initialize device list
        self._device_checkboxes = {}

        self._refresh_thread: QThread | None = None
        self._refresh_worker: _StreamdeckRefreshWorker | None = None

        # Ensure we never leave a QThread running during widget destruction.
        try:
            self.destroyed.connect(lambda *_: self._shutdown_refresh_thread())
        except Exception:
            pass

        # Apply global enable state to UI elements.
        try:
            self._apply_global_enabled_to_ui(bool(self.global_enable_checkbox.isChecked()))
        except Exception:
            pass

        self._refresh_devices()

    def _on_refresh_button_clicked(self) -> None:
        """Handle refresh button click: request bridge restart and refresh device list."""
        # If Streamdeck support is enabled, request a bridge restart so any
        # newly-enabled devices can be adopted. If disabled, we still refresh the
        # device list so the UI reflects what is plugged in.
        try:
            enabled = bool(self.global_enable_checkbox.isChecked())
        except Exception:
            enabled = True

        if enabled:
            try:
                self.streamdeck_refresh_requested.emit()
            except Exception:
                pass
            # Refresh the list after a delay to allow bridge restart.
            try:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(1500, self._refresh_devices)
                return
            except Exception:
                pass

        # Disabled: refresh immediately (no bridge restart).
        self._refresh_devices()

    def _shutdown_refresh_thread(self) -> None:
        """Best-effort shutdown of the background refresh thread."""
        thr = getattr(self, "_refresh_thread", None)
        if thr is None:
            return
        try:
            if thr.isRunning():
                try:
                    thr.quit()
                except Exception:
                    pass
                try:
                    thr.wait(1500)
                except Exception:
                    pass
        except Exception:
            pass
        # References are cleared in _on_refresh_finished when possible.
        try:
            if not thr.isRunning():
                self._refresh_worker = None
                self._refresh_thread = None
        except Exception:
            self._refresh_worker = None
            self._refresh_thread = None

    def _load_persisted_global_enable(self) -> None:
        """Load the global Streamdeck enable setting from QSettings."""
        settings = QSettings('StepD', 'TouchMusicPlayer')
        enabled = settings.value('streamdeck/enabled', True, type=bool)
        self.global_enable_checkbox.setChecked(enabled)

    def _save_persisted_global_enable(self) -> None:
        """Save the global Streamdeck enable setting to QSettings."""
        settings = QSettings('StepD', 'TouchMusicPlayer')
        settings.setValue('streamdeck/enabled', self.global_enable_checkbox.isChecked())

    def _on_global_enable_toggled(self, checked: bool) -> None:
        """Handle global enable/disable toggle."""
        try:
            self._save_persisted_global_enable()
        except Exception:
            pass
        try:
            self._apply_global_enabled_to_ui(bool(checked))
        except Exception:
            pass
        self.streamdeck_enabled_changed.emit(checked)

    def _apply_global_enabled_to_ui(self, enabled: bool) -> None:
        # Keep the list/refresh usable even when the service is disabled so the
        # UI can show connected/disconnected devices and users can pre-configure
        # per-device checkboxes.
        try:
            self.refresh_button.setEnabled(True)
        except Exception:
            pass
        try:
            self.device_list_widget.setEnabled(True)
        except Exception:
            pass

    def _refresh_devices(self) -> None:
        """Refresh the list of available Streamdeck devices (async)."""
        # Always allow scanning so the list updates even when the service is
        # disabled. We'll include a message when disabled.
        try:
            enabled = bool(self.global_enable_checkbox.isChecked())
        except Exception:
            enabled = True

        # Avoid overlapping refresh operations.
        try:
            if self._refresh_thread is not None and self._refresh_thread.isRunning():
                return
        except Exception:
            pass

        # UI: show loading indicator.
        msg = "Scanning Streamdeck devices..."
        if not enabled:
            msg = "Streamdeck support is disabled (service off). Scanning devices..."
        self._render_streamdeck_devices([], ok=True, err=msg)
        try:
            self.refresh_button.setEnabled(False)
        except Exception:
            pass

        # Create the worker thread with this widget as the parent so Qt owns it
        # and it won't be destroyed early.
        self._refresh_thread = QThread(self)
        self._refresh_worker = _StreamdeckRefreshWorker()
        self._refresh_worker.moveToThread(self._refresh_thread)
        self._refresh_thread.started.connect(self._refresh_worker.run)
        self._refresh_worker.done.connect(self._on_devices_refreshed)
        self._refresh_worker.done.connect(self._refresh_thread.quit)
        try:
            self._refresh_thread.finished.connect(self._on_refresh_finished)
        except Exception:
            pass
        try:
            self._refresh_thread.finished.connect(self._refresh_thread.deleteLater)
        except Exception:
            pass
        self._refresh_thread.start()

    def _on_devices_refreshed(self, devices: list, ok: bool, err: str) -> None:
        try:
            self._render_streamdeck_devices(devices, ok=bool(ok), err=str(err or ""))
        finally:
            try:
                self.refresh_button.setEnabled(bool(self.global_enable_checkbox.isChecked()))
            except Exception:
                pass

    def _on_refresh_finished(self) -> None:
        """Cleanup after the refresh thread exits."""
        try:
            if self._refresh_worker is not None:
                try:
                    self._refresh_worker.deleteLater()
                except Exception:
                    pass
        finally:
            self._refresh_worker = None
            self._refresh_thread = None

    def _render_streamdeck_devices(self, devices: list, ok: bool, err: str) -> None:
        # Clear existing device checkboxes
        for checkbox in self._device_checkboxes.values():
            try:
                checkbox.setParent(None)
                checkbox.deleteLater()
            except Exception:
                pass
        self._device_checkboxes.clear()

        # Clear layout
        while self.device_list_layout.count():
            item = self.device_list_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if err:
            msg = QLabel(str(err))
            msg.setStyleSheet('color: gray; font-style: italic;')
            self.device_list_layout.addWidget(msg)
            if not ok:
                # No devices to render if probing failed.
                return

        if not devices:
            if not err:
                no_devices_label = QLabel('No Streamdeck devices found')
                no_devices_label.setStyleSheet('color: gray; font-style: italic;')
                self.device_list_layout.addWidget(no_devices_label)
            return

        for d in devices:
            device_id = str(d.get('id') or '')
            device_type = str(d.get('type') or 'Unknown')
            serial = str(d.get('serial') or 'Unknown')
            hwid = str(d.get('hwid') or '')
            available = bool(d.get('available'))
            busy_reason = str(d.get('busy_reason') or '')

            # Create a horizontal layout for each device
            device_layout = QHBoxLayout()

            status_text = "Available" if available else (busy_reason or "Unavailable")
            short_id = hwid or device_id
            try:
                if len(short_id) > 48:
                    short_id = "…" + short_id[-48:]
            except Exception:
                short_id = hwid or device_id

            device_info = f'{device_type} - Serial: {serial} [{status_text}]\nID: {device_id}    HWID: {short_id}'
            device_label = QLabel(device_info)
            try:
                device_label.setToolTip(hwid or device_id)
            except Exception:
                pass
            if not available:
                device_label.setStyleSheet('color: orange;')
            device_layout.addWidget(device_label)

            enable_checkbox = QCheckBox('Enable')
            try:
                settings = QSettings('StepD', 'TouchMusicPlayer')
                enabled = settings.value(f'streamdeck/devices/{device_id}/enabled', True, type=bool)
                enable_checkbox.setChecked(bool(enabled))
            except Exception:
                enable_checkbox.setChecked(True)

            if not available:
                enable_checkbox.setChecked(False)
                enable_checkbox.setEnabled(False)
                enable_checkbox.setToolTip('Device is unavailable (possibly in use)')

            enable_checkbox.toggled.connect(
                lambda checked, dev_id=device_id: self._on_device_enable_toggled(dev_id, checked)
            )
            device_layout.addWidget(enable_checkbox)

            device_layout.addItem(QSpacerItem(10, 10, QtWidgets.QSizePolicy.Policy.Expanding))

            device_widget = QWidget()
            device_widget.setLayout(device_layout)
            self.device_list_layout.addWidget(device_widget)

            self._device_checkboxes[device_id] = enable_checkbox

    def _on_device_enable_toggled(self, device_id: str, checked: bool) -> None:
        """Handle device enable/disable toggle."""
        try:
            settings = QSettings('StepD', 'TouchMusicPlayer')
            settings.setValue(f'streamdeck/devices/{device_id}/enabled', checked)
        except Exception:
            pass
        self.device_enabled_changed.emit(device_id, checked)
        # Refresh the device list after a short delay to reflect any status changes (e.g., available -> in use).
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1000, self._refresh_devices)
        except Exception:
            pass


class SettingsWindow(QWidget):
    restart_output_signal = Signal()
    refresh_sound_devices_signal = Signal()
    
    save_output_settings_signal = Signal(dict)
    
    def __init__(
        self,
        parent: QWidget,
        height: int = 500,
        width: int = 450,
        pause: int = 1000,
        play: int = 100,
        *args,
        **kwargs,
    ):
        engine_adapter: Optional[EngineAdapter] = kwargs.pop("engine_adapter", None)
        super().__init__(*args, **kwargs)
        try:
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Fixed,
                QtWidgets.QSizePolicy.Policy.Fixed
            )
            self.parent = parent
            self.setWindowFlags(QtCore.Qt.WindowType.WindowStaysOnTopHint)
            self.setWindowTitle('Settings')

            self.setFixedSize(width, height)
            
            self.sample_rates = (48000.0, 44100.0, 32000.0, 22040.0, 16000.0, 11025.0, 8000.0)
            self.acceptable_apis = ['MME', 'Windows DirectSound', 'ASIO', 'Windows WASAPI']

            self.settings = SaveSettings("Settings.json")
            self.app_settings = self.settings.get_settings()
            self.settings_signals = SettingSignals()

            # Boundary layer to audio service (queue-based). Optional for now.
            self.engine_adapter: Optional[EngineAdapter] = engine_adapter
  
            self.fade_out_dur = int(pause)
            self.fade_in_dur = int(play)
            
            self.sample_rate = 48000
            
            self.usable_devices:dict = {}
            self.show_all_devices: bool = False  # User preference: show all devices vs. filtered
            try:
                self.devices = sd.query_devices()
            except Exception:
                self.devices = []
            try:
                self.apis = sd.query_hostapis()
            except Exception:
                self.apis = ()

            # Guard to prevent signal handlers from firing during initial population/restore.
            self._initializing = True

            # Root layout contains a tab bar so we can expand settings cleanly.
            self.root_layout = QVBoxLayout()
            self.setLayout(self.root_layout)

            # Avoid double margins now that the old layout lives inside a tab.
            self.root_layout.setContentsMargins(0, 0, 0, 0)
            self.root_layout.setSpacing(0)

            self.tabs = QTabWidget()
            self.root_layout.addWidget(self.tabs)

            # Audio tab: contains all existing settings, unchanged (just moved under this tab).
            self.audio_tab = QWidget()
            self.main_layout = QVBoxLayout()
            self.audio_tab.setLayout(self.main_layout)
            self.tabs.addTab(self.audio_tab, 'Audio')

            # Keyboard Shortcuts tab: UI-only display of mappings.
            self.keyboard_shortcuts_tab = KeyboardShortcutsTab(self)
            self.tabs.addTab(self.keyboard_shortcuts_tab, 'Keyboard Shortcuts')

            # Streamdeck tab: configure Streamdeck device support.
            self.streamdeck_tab = StreamdeckTab(self)
            self.tabs.addTab(self.streamdeck_tab, 'Streamdeck')

            self.slider_layout = QHBoxLayout()
            self.v_layoutL = QVBoxLayout()
            self.v_layoutM = QVBoxLayout()
            self.v_layoutR = QVBoxLayout()
            crossfade_label = QLabel('Crossfade Duration Settings')
            self.main_layout.addWidget(crossfade_label)

            self.main_layout.addLayout(self.slider_layout)
            
            self.slider_layout.addLayout(self.v_layoutL)
            self.slider_layout.addLayout(self.v_layoutM)
            self.slider_layout.addLayout(self.v_layoutR)

            self.spacer = QSpacerItem(100, 300, QtWidgets.QSizePolicy.Policy.Fixed)

            slider_height = 20

            self.fade_out_label = QLabel('FADE\nOUT')
            self.fade_out_label.setFont(QFont('arial', 6))
            self.fade_out_line_edit = QLineEdit('0')
            self.fade_out_line_edit.setFixedSize(30,25)
            self.fade_out_line_edit.setStyleSheet('border: 1px solid black;')
            self.fade_out_line_edit.returnPressed.connect(self.fade_out_line_edit_handler)
            self.fade_out_slider = QSlider(QtCore.Qt.Orientation.Horizontal)
            self.fade_out_slider.setFixedHeight(slider_height)
            self.fade_out_slider.setRange(1,2000)
            self.fade_out_slider.setValue(self.fade_out_dur)
            self.fade_out_slider.valueChanged.connect(self.update_fade_out_dur)
            self.fade_out_slider.sliderReleased.connect(self._send_transition_fade_settings)

        
            self.fade_in_label = QLabel('FADE\nIN')
            self.fade_in_label.setFont(QFont('arial', 6))
            self.fade_in_line_edit = QLineEdit('0')
            self.fade_in_line_edit.setFixedSize(30,25)
            self.fade_in_line_edit.setStyleSheet('border: 1px solid black;')
            self.fade_in_line_edit.returnPressed.connect(self.fade_in_line_edit_handler)
            self.fade_in_slider = QSlider(QtCore.Qt.Orientation.Horizontal)
            self.fade_in_slider.setFixedHeight(slider_height)
            self.fade_in_slider.setRange(1,2000)
            self.fade_in_slider.valueChanged.connect(self.update_fade_in_dur)
            self.fade_in_slider.setValue(self.fade_in_dur)
            self.fade_in_slider.sliderReleased.connect(self._send_transition_fade_settings)

            self.v_layoutL.addWidget(self.fade_in_label)
            self.v_layoutM.addWidget(self.fade_in_line_edit)
            self.v_layoutR.addWidget(self.fade_in_slider)
            
            self.v_layoutL.addWidget(self.fade_out_label)
            self.v_layoutM.addWidget(self.fade_out_line_edit)
            self.v_layoutR.addWidget(self.fade_out_slider)


            spacer = QSpacerItem(20,70)

            self.main_layout.addItem(spacer)

            self.refresh_layout = QHBoxLayout()
            self.refresh_outputs_button = QPushButton('REFRESH OUTPUT COMBO BOXES')
            self.refresh_layout.addWidget(self.refresh_outputs_button)
            self.main_layout.addLayout(self.refresh_layout)
            self.refresh_outputs_button.clicked.connect(self.refresh_devices)
            
            # Device refresh worker (runs enumeration off the UI thread).
            self._device_refresh_thread: QThread | None = None
            self._device_refresh_worker: _DeviceRefreshWorker | None = None
            self._device_refresh_in_flight: bool = False

            # Optional: listen for OS device changes and refresh while window is open.
            # (Best-effort; safe even if unavailable on some platforms.)
            self._qt_media_devices = None
            try:
                self._qt_media_devices = QMediaDevices(self)
                sig = getattr(self._qt_media_devices, "audioOutputsChanged", None)
                if sig is not None:
                    sig.connect(self._on_qt_audio_outputs_changed)
            except Exception:
                self._qt_media_devices = None

            # Checkbox to show all devices (including virtual/Dante)
            device_filter_layout = QHBoxLayout()
            self.show_all_devices_checkbox = QCheckBox('Show All Devices (including Virtual)')
            self.show_all_devices_checkbox.setChecked(False)
            self.show_all_devices_checkbox.stateChanged.connect(self._on_show_all_devices_changed)
            device_filter_label = QLabel('Device Filter')
            device_filter_layout.addWidget(device_filter_label)
            device_filter_layout.addWidget(self.show_all_devices_checkbox)
            self.main_layout.addLayout(device_filter_layout)

            main_output_label = QLabel('Main Output')
            self.main_layout.addWidget(main_output_label)

            self.audio_output_combo = QComboBox()
            self.audio_output_combo.setFixedWidth(400)
            self.audio_output_combo.addItem('MAIN AUDIO OUTPUT')
            self.main_layout.addWidget(self.audio_output_combo, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)

            editor_label = QLabel('Editor Output')
            self.main_layout.addWidget(editor_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
            self.editor_audio_output_combo = QComboBox()
            self.editor_audio_output_combo.setFixedWidth(400)
            self.editor_audio_output_combo.addItem('EDITOR AUDIO OUTPUT')
            self.main_layout.addWidget(self.editor_audio_output_combo, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
            self.populate_audio_combo_box()
            self.audio_output_combo.currentIndexChanged.connect(self.main_output_changed)
            self.editor_audio_output_combo.currentIndexChanged.connect(self.editor_output_changed)
            self.main_output_device = {}
            self.editor_output_device = {}

            self.button_banks_settings_layout = QHBoxLayout()
            self.button_banks_setting_label = QLabel('Adjust Number of Rows and Columns')
            self.rows_label = QLabel('Rows:')
            self.columns_label = QLabel('Columns:')
            self.rows_spinbox = QSpinBox()
            self.rows_spinbox.setFixedSize(60, 30)
            font = QFont('Arial', 12)
            self.rows_spinbox.setFont(font)
            self.rows_spinbox.setRange(1, 5)
            self.columns_spinbox = QSpinBox()
            self.columns_spinbox.setFixedSize(60, 30)
            self.columns_spinbox.setFont(font)
            self.columns_spinbox.setRange(1,10)
            # New UI uses MainWindow.bank (ButtonBankWidget). Legacy uses buttonBanksWidget.
            try:
                self.rows_spinbox.setValue(int(getattr(getattr(self.parent, "bank", None), "rows", 3)))
            except Exception:
                try:
                    self.rows_spinbox.setValue(int(getattr(getattr(self.parent, "buttonBanksWidget", None), "rows", 3)))
                except Exception:
                    self.rows_spinbox.setValue(3)

            try:
                self.columns_spinbox.setValue(int(getattr(getattr(self.parent, "bank", None), "cols", 8)))
            except Exception:
                try:
                    self.columns_spinbox.setValue(int(getattr(getattr(self.parent, "buttonBanksWidget", None), "columns", 8)))
                except Exception:
                    self.columns_spinbox.setValue(8)
            self.rows_spinbox.valueChanged.connect(self.change_row_columns)
            self.columns_spinbox.valueChanged.connect(self.change_row_columns)
            self.main_layout.addWidget(self.button_banks_setting_label)
            row_columns_spacer = QSpacerItem(300, 10)
            # self.button_banks_settings_layout.addItem(row_columns_spacer)
            self.button_banks_settings_layout.addWidget(self.rows_label)
            self.button_banks_settings_layout.addWidget(self.rows_spinbox)
            self.button_banks_settings_layout.addWidget(self.columns_label)
            self.button_banks_settings_layout.addWidget(self.columns_spinbox)
            self.button_banks_settings_layout.addItem(row_columns_spacer)
            self.main_layout.addLayout(self.button_banks_settings_layout)

            self.restore_saved_settings()

            self.update_fade_out_dur()
            self.update_fade_in_dur()

            # Ensure engine receives the currently loaded transition fades.
            self._send_transition_fade_settings()

            self._initializing = False

            # Soft refresh after the window is created so device lists are current,
            # without blocking the UI or disrupting playback.
            try:
                QtCore.QTimer.singleShot(0, self.refresh_devices)
            except Exception:
                pass
            
        except Exception as e:
            print(e)

    def restore_saved_settings(self):
        try:
            #recall settings
            if 'pause_fade_dur' in self.app_settings:
                self.fade_out_dur = int(self.app_settings['pause_fade_dur'])
                self.fade_out_slider.setValue(self.fade_out_dur)
                self.fade_out_line_edit.setText(str(self.fade_out_dur))
            elif 'fade_out_duration' in self.app_settings:
                self.fade_out_dur = int(self.app_settings['fade_out_duration'])
                self.fade_out_slider.setValue(self.fade_out_dur)
                self.fade_out_line_edit.setText(str(self.fade_out_dur))

            if 'play_fade_dur' in self.app_settings:
                self.fade_in_dur = int(self.app_settings['play_fade_dur'])
                self.fade_in_slider.setValue(self.fade_in_dur)
                self.fade_in_line_edit.setText(str(self.fade_in_dur))
            elif 'fade_in_duration' in self.app_settings:
                self.fade_in_dur = int(self.app_settings['fade_in_duration'])
                self.fade_in_slider.setValue(self.fade_in_dur)
                self.fade_in_line_edit.setText(str(self.fade_in_dur))

            # ------------------------
            # Output restore w/ fallback
            # ------------------------

            def _select_output(combo: QComboBox, saved: object, label: str) -> dict:
                """Select saved output if available; else fall back and warn."""
                saved_name = None
                saved_hostapi = None
                saved_index = None

                if isinstance(saved, (list, tuple)) and saved:
                    # Backward compat:
                    # - old: [name, hostapi_name, sample_rate]
                    # - new: [index, name, hostapi_name, sample_rate]
                    if len(saved) >= 4:
                        saved_index = saved[0]
                        saved_name = saved[1]
                        saved_hostapi = saved[2]
                    elif len(saved) >= 2:
                        saved_name = saved[0]
                        saved_hostapi = saved[1]

                def _find_by_index(idx: int | None) -> int:
                    if idx is None:
                        return -1
                    try:
                        idx = int(idx)
                    except Exception:
                        return -1
                    for i in range(1, combo.count()):
                        try:
                            d = combo.itemData(i)
                            if isinstance(d, dict) and int(d.get('index')) == idx:
                                return i
                        except Exception:
                            continue
                    return -1

                def _find_by_text(name: str | None, hostapi: str | None) -> int:
                    if not name or not hostapi:
                        return -1
                    search = f"{name}, {hostapi}"
                    try:
                        return combo.findText(search, flags=Qt.MatchFlag.MatchContains)
                    except Exception:
                        return -1

                # Try to find the saved device
                selected = _find_by_index(saved_index)
                if selected < 1:
                    selected = _find_by_text(saved_name, saved_hostapi)

                missing_saved = False
                if selected < 1:
                    missing_saved = bool(saved_name)
                    # Fall back to system default output
                    try:
                        default_idx = sd.default.device[1]
                        default_dev = sd.query_devices(default_idx)
                        api = self.apis[default_dev['hostapi']] if self.apis else {"name": ""}
                        selected = combo.findText(default_dev['name'] + ', ' + api['name'], flags=Qt.MatchFlag.MatchContains)
                    except Exception:
                        selected = -1

                # Last resort: first available device
                if selected < 1 and combo.count() > 1:
                    selected = 1

                try:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(selected)
                finally:
                    combo.blockSignals(False)

                chosen = combo.itemData(selected) if selected >= 1 else {}
                if not isinstance(chosen, dict):
                    chosen = {}

                if missing_saved:
                    try:
                        QMessageBox.warning(
                            self,
                            "Output device unavailable",
                            f"The previously saved {label} output device is not available.\n\n"
                            f"Saved: {saved_name}, {saved_hostapi}\n"
                            f"Using: {chosen.get('name', 'Unknown')}, {chosen.get('hostapi_name', 'Unknown')}",
                        )
                    except Exception:
                        pass

                    # Persist fallback so next launch is consistent.
                    try:
                        if chosen.get('index') is not None:
                            self.settings.set_setting(label, [chosen.get('index'), chosen.get('name'), chosen.get('hostapi_name'), chosen.get('sample_rate')])
                            self.settings.save_settings()
                    except Exception:
                        pass

                return chosen

            # Restore selections (no engine restart on open)
            saved_main = self.app_settings.get('Main_Output')
            saved_editor = self.app_settings.get('Editor_Output')

            self.main_output_device = _select_output(self.audio_output_combo, saved_main, 'Main_Output')
            self.editor_output_device = _select_output(self.editor_audio_output_combo, saved_editor, 'Editor_Output')

            try:
                self.parent.main_output_device = self.main_output_device
            except Exception:
                pass
            try:
                self.parent.editor_output_device = self.editor_output_device
            except Exception:
                pass

            # (Editor output handled by _select_output above)

            if 'rows' in self.app_settings:
                rows = self.app_settings['rows']
                self.rows_spinbox.setValue(rows)

            if 'columns' in self.app_settings:
                columns = self.app_settings['columns']
                self.columns_spinbox.setValue(columns)
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
            
    def restore_outputs(self):
        if 'Main_Output' in self.app_settings:
            device = self.app_settings['Main_Output']
            device_name = device[0]
            device_hostapi = device[1] #MME, WINDOWS DIRECT SOUND, ASIO etc only the index number is saved
            
            device_search_text = device_name + ', ' + device_hostapi
            
            selected_index = self.audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
            self.audio_output_combo.setCurrentIndex(selected_index)
            self.main_output_device = device
            
        else:
            index = sd.default.device[1]
            device = sd.query_devices(index)
            api = self.apis[device['hostapi']]
            
            device_search_text = device['name'] + ', ' + api['name']
            
            selected_index = self.audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
            self.audio_output_combo.setCurrentIndex(selected_index)
            self.main_output_device = device

        if 'Editor_Output' in self.app_settings:
            device = self.app_settings['Editor_Output']
            device_name = device[0]
            device_hostapi = device[1]  #MME, WINDOWS DIRECT SOUND, ASIO etc
            
            device_search_text = device_name + ', ' + device_hostapi
            selected_index = self.editor_audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
            self.editor_audio_output_combo.setCurrentIndex(selected_index)
            self.editor_output_device = device
                    
        else:
            index = sd.default.device[1]
            device = sd.query_devices(index)
            api = self.apis[device['hostapi']]
            device_search_text = device['name'] + ', ' + api['name']
            selected_index = self.editor_audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
            self.editor_audio_output_combo.setCurrentIndex(selected_index)
            self.editor_output_device = device

    def _is_real_device(self, device: dict, name: str) -> bool:
        """
        Filter out virtual devices and loopback endpoints.
        
        If show_all_devices is True, allow all devices with output.
        Otherwise, looks for common virtual/fake device patterns and whitelists pro audio.
        
        Returns True if device should be shown.
        """
        name_lower = name.lower()
        
        # Whitelist professional virtual audio devices (must come before exclusion patterns)
        whitelist_patterns = [
            'dante',           # Dante virtual audio
            'network audio',   # Generic network audio
            'madi',            # Multichannel Audio Digital Interface
            'aes67',           # AES67 audio networking
        ]
        for pattern in whitelist_patterns:
            if pattern in name_lower:
                return True  # Whitelist: allow pro audio networking
        
        # If show_all_devices is enabled, allow any device with output channels
        if self.show_all_devices:
            return device.get('max_output_channels', 0) > 0
        
        # Virtual/loopback patterns to exclude
        virtual_patterns = [
            'virtual',
            'loopback',
            'stereo mix',
            'what u hear',
            'wave out mix',
            'microphone',  # Exclude input devices
            'input',
            'mono',  # Prefer stereo
            'dummy',
            'none',
            'disabled',
            'cable',  # VB-Cable, VB-Audio, etc.
        ]
        
        for pattern in virtual_patterns:
            if pattern in name_lower:
                return False
        
        # Must have at least 2 output channels to be useful
        if device.get('max_output_channels', 0) < 2:
            return False
        
        # Device must not be marked as an input-only device
        if device.get('max_output_channels', 0) == 0:
            return False
        
        return True

    def populate_audio_combo_box(self):     
        try:
            self.audio_output_combo.clear()
            self.editor_audio_output_combo.clear()

            self.audio_output_combo.addItem('Main Audio Output')
            self.editor_audio_output_combo.addItem('Editor Audio Output')

            self.usable_devices.clear()
            
            for api in self.apis:
                if api['name'] in self.acceptable_apis:
                    for device_idx in api['devices']:
                        device = self.devices[device_idx]
                        device_name = device.get('name', '')
                        
                        # Filter: must be output device with 2+ channels and be "real"
                        if (device.get('max_output_channels', 0) > 0 and 
                            self._is_real_device(device, device_name)):
                            
                            # Test sample rate compatibility
                            for sample_rate in self.sample_rates:
                                try:
                                    sd.check_output_settings(device_idx, channels=2, samplerate=sample_rate)
                                    usable_device = self.devices[device_idx].copy()
                                    usable_device['sample_rate'] = sample_rate
                                    usable_device['hostapi_name'] = api['name']
                                    usable_device['index'] = device_idx
                                    self.usable_devices[device_idx] = usable_device
                                    break
                                
                                except:
                                    pass
            
            
            for device_idx in self.usable_devices:
                device = self.usable_devices[device_idx]
                api_idx = device.get('hostapi', 0)
                api = self.apis[api_idx]
                self.audio_output_combo.addItem(device['name'] +', ' + api['name'], userData=device)
                self.editor_audio_output_combo.addItem(device['name'] + ', ' + api['name'], userData=device)

        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')

    def _ensure_device_refresh_worker(self) -> None:
        if self._device_refresh_thread is not None and self._device_refresh_worker is not None:
            return
        try:
            self._device_refresh_thread = QThread(self)
            self._device_refresh_worker = _DeviceRefreshWorker()
            self._device_refresh_worker.moveToThread(self._device_refresh_thread)
            self._device_refresh_worker.done.connect(self._on_device_refresh_done)
            self._device_refresh_thread.start()
        except Exception:
            self._device_refresh_thread = None
            self._device_refresh_worker = None

    def _select_combo_by_device(self, combo: QComboBox, device: dict | None) -> int:
        """Return an index in combo matching device (by index, else name+hostapi)."""
        if not device or not isinstance(device, dict):
            return -1

        try:
            target_index = device.get("index")
        except Exception:
            target_index = None
        if target_index is not None:
            try:
                target_index = int(target_index)
            except Exception:
                target_index = None

        if target_index is not None:
            for i in range(1, combo.count()):
                try:
                    d = combo.itemData(i)
                    if isinstance(d, dict) and int(d.get("index")) == int(target_index):
                        return i
                except Exception:
                    continue

        try:
            name = (device.get("name") or "").strip()
            hostapi_name = (device.get("hostapi_name") or "").strip()
        except Exception:
            name = ""
            hostapi_name = ""

        if name and hostapi_name:
            search = f"{name}, {hostapi_name}"
            try:
                idx = combo.findText(search, flags=Qt.MatchFlag.MatchContains)
            except Exception:
                idx = -1
            if idx >= 1:
                return idx

        return -1
            
    def refresh_devices(self):
        """Refresh audio device lists without blocking the GUI.

        Uses a background worker to call sounddevice enumeration. Does not modify
        playback state or engine output configuration.
        """

        try:
            self.refresh_outputs_button.setEnabled(False)
        except Exception:
            pass

        # Prevent overlapping refresh calls (checkbox toggles can spam).
        if getattr(self, "_device_refresh_in_flight", False):
            return
        self._device_refresh_in_flight = True

        self._ensure_device_refresh_worker()
        if self._device_refresh_worker is None:
            self._device_refresh_in_flight = False
            try:
                self.refresh_outputs_button.setEnabled(True)
            except Exception:
                pass
            return

        try:
            QtCore.QMetaObject.invokeMethod(self._device_refresh_worker, "run", QtCore.Qt.ConnectionType.QueuedConnection)
        except Exception:
            # Fallback: run in UI thread (still best-effort, should be quick).
            try:
                self._device_refresh_worker.run()
            except Exception:
                self._device_refresh_in_flight = False
                try:
                    self.refresh_outputs_button.setEnabled(True)
                except Exception:
                    pass
        
    def re_populate_audio_combo_box(self, device_list:list, api_dict:tuple):
        # NOTE: called from UI thread via worker signal.
        prev_main = None
        prev_editor = None
        try:
            prev_main = self.audio_output_combo.currentData()
        except Exception:
            prev_main = None
        try:
            prev_editor = self.editor_audio_output_combo.currentData()
        except Exception:
            prev_editor = None

        # Always re-enable UI controls, even if something throws.
        try:
            self.devices = device_list
            self.apis = api_dict

            # Block signals instead of disconnect/reconnect (more reliable).
            self.audio_output_combo.blockSignals(True)
            self.editor_audio_output_combo.blockSignals(True)
            self.audio_output_combo.setEnabled(False)
            self.editor_audio_output_combo.setEnabled(False)

            self.populate_audio_combo_box()

            # Try to keep the user's current selection stable across refresh.
            idx = self._select_combo_by_device(self.audio_output_combo, prev_main)
            if idx >= 1:
                self.audio_output_combo.setCurrentIndex(idx)
            idx = self._select_combo_by_device(self.editor_audio_output_combo, prev_editor)
            if idx >= 1:
                self.editor_audio_output_combo.setCurrentIndex(idx)

            # Update cached devices to whatever is selected now.
            try:
                self.main_output_device = self.audio_output_combo.currentData() or {}
            except Exception:
                self.main_output_device = {}
            try:
                self.editor_output_device = self.editor_audio_output_combo.currentData() or {}
            except Exception:
                self.editor_output_device = {}

        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
        finally:
            try:
                self.audio_output_combo.setEnabled(True)
                self.editor_audio_output_combo.setEnabled(True)
                self.audio_output_combo.blockSignals(False)
                self.editor_audio_output_combo.blockSignals(False)
            except Exception:
                pass

            try:
                self.refresh_outputs_button.setEnabled(True)
            except Exception:
                pass

            try:
                self._device_refresh_in_flight = False
            except Exception:
                pass

    def _on_device_refresh_done(self, device_list: list, api_dict: tuple, ok: bool, err: str) -> None:
        # Even on failure, make sure UI isn't left disabled.
        if not ok:
            try:
                print(f"Device refresh failed: {err}")
            except Exception:
                pass
            try:
                self.audio_output_combo.setEnabled(True)
                self.editor_audio_output_combo.setEnabled(True)
            except Exception:
                pass
            try:
                self.refresh_outputs_button.setEnabled(True)
            except Exception:
                pass
            try:
                self._device_refresh_in_flight = False
            except Exception:
                pass
            return

        self.re_populate_audio_combo_box(device_list, api_dict)

    def _on_qt_audio_outputs_changed(self) -> None:
        # Best-effort, non-blocking refresh when OS reports changes.
        try:
            self.refresh_devices()
        except Exception:
            pass
            
    def check_device_capabilites(self, device=sd):
        sample_rates = [8000.0, 11025.0, 16000.0, 22050.0, 32000.0, 44100.0, 48000.0] #96000.0
        supports_all_sample_rates = False
        for sample_rate in sample_rates:
            try:
                sd.check_output_settings(device=device['index'], samplerate=sample_rate)
                supports_all_sample_rates = True
            except:
                supports_all_sample_rates = False
                return supports_all_sample_rates
                
        return supports_all_sample_rates
            
    def main_output_changed(self):
        try:
            if getattr(self, "_initializing", False):
                return
            index = self.audio_output_combo.currentIndex()
            if index <= 0:
                return
            device = self.audio_output_combo.itemData(index)
            if not device:
                return
            
            self.main_output_device = device
            print(f'main output: {device}')
            self.sample_rate = device['sample_rate']

            # Persist new schema that includes index for reliable engine routing.
            self.settings.set_setting('Main_Output', [device.get('index'), device['name'], device['hostapi_name'], device['sample_rate']])
            self.settings_signals.main_output_signal.emit(self.main_output_device['index'], self.main_output_device['sample_rate'])
            self.parent.main_output_device = self.audio_output_combo.currentData()
            self.settings.save_settings()

            # Send to audio engine via queue boundary.
            if self.engine_adapter is not None:
                try:
                    self.engine_adapter.set_output_device(device.get('index'))
                except Exception:
                    pass
                try:
                    # Re-open stream with sample rate known-good for this device.
                    self.engine_adapter.set_output_config(
                        sample_rate=int(device['sample_rate']),
                        channels=2,
                        block_frames=2048,
                    )
                except Exception:
                    pass
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
            
    def editor_output_changed(self):
        try:
            if getattr(self, "_initializing", False):
                return
            index = self.editor_audio_output_combo.currentIndex()
            if index <= 0:
                return
            device = self.editor_audio_output_combo.itemData(index)
            if not device:
                return
            
            self.editor_output_device = device

            # Persist new schema including index.
            self.settings.set_setting('Editor_Output', [device.get('index'), device['name'], device['hostapi_name'], device['sample_rate']])
            self.settings_signals.editor_output_signal.emit(device['index'], device['sample_rate'])
            self.parent.editor_output_device = self.editor_audio_output_combo.currentData()
            self.settings.save_settings()
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')

        
#
    def update_fade_out_dur(self):
        dur = self.fade_out_slider.value()
        self.fade_out_dur = dur
        self.fade_out_line_edit.setText(str(dur))

    def update_fade_in_dur(self):
        dur = self.fade_in_slider.value()
        self.fade_in_dur = dur
        self.fade_in_line_edit.setText(str(dur))

    def _send_transition_fade_settings(self):
        """Send current fade durations to the audio engine via queued command."""
        if self.engine_adapter is None:
            return
        try:
            self.engine_adapter.set_transition_fade_durations(
                fade_in_ms=int(self.fade_in_dur),
                fade_out_ms=int(self.fade_out_dur),
            )
        except Exception:
            pass

    def fade_in_line_edit_handler(self):
        text = self.fade_in_line_edit.text()
        try:
            t = int(text)
            self.fade_in_slider.setValue(t)
            self.fade_in_dur = t
            self._send_transition_fade_settings()

        except:
            self.fade_in_line_edit.setText('10')
            self.fade_in_slider.setValue(10)
    
    def fade_out_line_edit_handler(self):
        text = self.fade_out_line_edit.text()
        try:
            t = int(text)
            self.fade_out_slider.setValue(t)
            self.fade_out_dur=t
            self._send_transition_fade_settings()

        except:
            self.fade_out_line_edit.setText('10')
            self.fade_out_slider.setValue(10)

    def change_row_columns(self):
        banks=10
        rows=self.rows_spinbox.value()
        columns=self.columns_spinbox.value()
        self.settings_signals.change_rows_and_columns_signal.emit(rows, columns)

    def _on_show_all_devices_changed(self, state: int) -> None:
        """Handler for show_all_devices checkbox state change."""
        self.show_all_devices = bool(state)
        # Refresh device lists immediately
        self.refresh_devices()

    def closeEvent(self,event): 
        try:
            #save settings on closing the settings window
            # Maintain backward compatibility keys + the newer keys used by this window.
            self.settings.set_setting('fade_in_duration', int(self.fade_in_dur))
            self.settings.set_setting('fade_out_duration', int(self.fade_out_dur))
            self.settings.set_setting('play_fade_dur', int(self.fade_in_dur))
            self.settings.set_setting('pause_fade_dur', int(self.fade_out_dur))

            if isinstance(self.editor_output_device, dict) and self.editor_output_device:
                self.settings.set_setting('Editor_Output', [self.editor_output_device.get('index'), self.editor_output_device.get('name'), self.editor_output_device.get('hostapi_name'), self.editor_output_device.get('sample_rate')])
            if isinstance(self.main_output_device, dict) and self.main_output_device:
                self.settings.set_setting('Main_Output', [self.main_output_device.get('index'), self.main_output_device.get('name'), self.main_output_device.get('hostapi_name'), self.main_output_device.get('sample_rate')])

            self.settings.set_setting('rows', self.rows_spinbox.value())
            self.settings.set_setting('columns', self.columns_spinbox.value())
            self.settings.save_settings()

            # Ensure engine has final values (in case the user typed values but didn't release slider).
            self._send_transition_fade_settings()
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')

        # Best-effort cleanup of refresh thread.
        try:
            if self._device_refresh_thread is not None and self._device_refresh_thread.isRunning():
                self._device_refresh_thread.quit()
                self._device_refresh_thread.wait(1500)
        except Exception:
            pass

        # Best-effort cleanup of Streamdeck refresh thread.
        try:
            tab = getattr(self, 'streamdeck_tab', None)
            if tab is not None:
                try:
                    tab._shutdown_refresh_thread()
                except Exception:
                    pass
        except Exception:
            pass

        event.accept()


