from __future__ import annotations

import math
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot


@dataclass(frozen=True)
class _KeyRender:
    key: int
    text: str
    active_level: float  # 0..1 (0=inactive)
    icon_path: Optional[str] = None
    bg_rgb: Optional[tuple[int, int, int]] = None
    fg_rgb: Optional[tuple[int, int, int]] = None
    corner_text: str = ""


class StreamDeckXLBridge(QObject):
    """Bridge Stream Deck XL keys to GUI button banks + transport.

    Design goals:
    - Never block the Qt thread.
    - Never touch Qt widgets from non-Qt threads.
    - Stream Deck USB I/O happens on a dedicated worker thread.

    Layout mapping (Stream Deck XL is 4 rows x 8 cols, keys 0..31 row-major):
    - Keys 0..23: 3x8 SoundFileButtons (index 1..24 row-major)
    - Bottom row (24..31):
        24: Bank -
        25: Bank +
        26: Play
        27: Pause
        28: Stop
        29: Next (GUI bank transport_next)
        30: Loop (toggles loop + override)
        31: GUI Sync (toggles bank mode)

    Bank modes:
    - sync: displayed bank follows GUI BankSelectorWidget
    - independent: displayed bank is controlled by Stream Deck Bank-/Bank+
    """

    class BankMode:
        SYNC = "sync"
        INDEPENDENT = "independent"

    connected_changed = Signal(bool)
    _deck_key_pressed = Signal(int)

    def __init__(
        self,
        bank_selector: QObject,
        engine_adapter: QObject,
        play_controls: Optional[QObject] = None,
        *,
        mode: str = BankMode.SYNC,
        show_corner_label: bool = False,
        parent: Optional[QObject] = None,
        pulse_period_s: float = 1.2,
        pulse_fps: float = 8.0,
    ) -> None:
        super().__init__(parent)
        self._bank_selector = bank_selector
        self._engine_adapter = engine_adapter
        self._play_controls = play_controls

        self._mode = str(mode)
        self._show_corner_label = bool(show_corner_label)
        self._pulse_period_s = float(pulse_period_s)
        self._pulse_interval_ms = int(max(50.0, 1000.0 / float(pulse_fps)))

        self._deck = None
        self._key_size: tuple[int, int] = (72, 72)

        self._display_bank_index: int = 0
        self._force_full_redraw = True
        self._dirty_keys: set[int] = set()

        # Stream Deck-only visual selection for transport keys.
        # When one of Play/Pause/Stop/Loop is pressed, it brightens until another
        # of those keys is pressed.
        self._transport_selected_key: Optional[int] = None
        self._last_transport_selected_key: Optional[int] = None
        self._active_cue_ids: set[str] = set()
        self._transport_state: str = "playing"  # "playing" | "paused" | "stopped"
        self._last_play_highlight: Optional[bool] = None

        self._last_snapshot: dict[int, tuple[str, bool]] = {}

        # Resolve asset directory (matches PlayControls: 'Assets\\*.png')
        try:
            self._assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Assets")
        except Exception:
            self._assets_dir = "Assets"

        self._key_event_q: "queue.Queue[tuple[int, bool]]" = queue.Queue(maxsize=256)
        self._io_q: "queue.Queue[_KeyRender]" = queue.Queue(maxsize=512)

        self._io_thread = threading.Thread(target=self._io_loop, name="streamdeck-io", daemon=True)
        self._io_stop = threading.Event()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(10)
        self._poll_timer.timeout.connect(self._drain_key_events)

        self._render_timer = QTimer(self)
        self._render_timer.setInterval(self._pulse_interval_ms)
        self._render_timer.timeout.connect(self._render_tick)

        # Thread-safe delivery: StreamDeck callbacks occur off the Qt thread.
        # Emitting a Qt signal here queues the call to the main thread.
        self._deck_key_pressed.connect(self._handle_key_press)

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def start(self) -> None:
        """Attempt to connect to the first Stream Deck and begin processing."""
        if self._deck is not None:
            return

        deck = self._try_open_first_deck()
        if deck is None:
            self.connected_changed.emit(False)
            return

        self._deck = deck
        try:
            fmt = self._deck.key_image_format() or {}
            size = fmt.get("size")
            if isinstance(size, (list, tuple)) and len(size) == 2:
                self._key_size = (int(size[0]), int(size[1]))
        except Exception:
            pass

        try:
            self._deck.set_key_callback(self._on_key_change)
        except Exception:
            pass

        self._sync_display_bank_from_gui(initial=True)
        self._wire_signals()

        self._io_stop.clear()
        self._io_thread.start()

        # Key presses are delivered via _deck_key_pressed; no polling needed.
        self._render_timer.start()

        self._force_full_redraw = True
        self.connected_changed.emit(True)

    def stop(self) -> None:
        """Stop threads and close the Stream Deck device."""
        try:
            self._poll_timer.stop()
        except Exception:
            pass
        try:
            self._render_timer.stop()
        except Exception:
            pass

        self._io_stop.set()
        try:
            # Unblock the IO thread promptly.
            self._io_q.put_nowait(_KeyRender(key=-1, text="", active_level=0.0))
        except Exception:
            pass

        try:
            if self._deck is not None:
                try:
                    self._deck.reset()
                except Exception:
                    pass
                try:
                    self._deck.close()
                except Exception:
                    pass
        finally:
            self._deck = None
            self.connected_changed.emit(False)

    def set_mode(self, mode: str) -> None:
        mode = str(mode)
        if mode not in (self.BankMode.SYNC, self.BankMode.INDEPENDENT):
            return
        if mode == self._mode:
            return
        self._mode = mode
        if self._mode == self.BankMode.SYNC:
            self._sync_display_bank_from_gui(initial=False)
        self._force_full_redraw = True

    def mode(self) -> str:
        return self._mode

    def set_show_corner_label(self, enabled: bool) -> None:
        """Enable/disable the small top-left bank/button index overlay."""
        enabled = bool(enabled)
        if enabled == self._show_corner_label:
            return
        self._show_corner_label = enabled
        self._force_full_redraw = True

    # ---------------------------------------------------------------------
    # Device discovery / callback
    # ---------------------------------------------------------------------

    def _try_open_first_deck(self):
        try:
            from StreamDeck.DeviceManager import DeviceManager
        except Exception:
            return None

        try:
            decks = DeviceManager().enumerate() or []
        except Exception:
            return None

        if not decks:
            return None

        deck = decks[0]
        try:
            deck.open()
        except Exception:
            return None

        try:
            deck.reset()
        except Exception:
            pass
        try:
            deck.set_brightness(35)
        except Exception:
            pass
        return deck

    def _on_key_change(self, _deck, key: int, state: bool) -> None:
        # Called from the StreamDeck library thread(s). Do not touch Qt here.
        # Use queued Qt signal for immediate main-thread handling.
        try:
            if bool(state):
                self._deck_key_pressed.emit(int(key))
        except Exception:
            return

    # ---------------------------------------------------------------------
    # Qt-thread event handling (safe to touch widgets)
    # ---------------------------------------------------------------------

    def _wire_signals(self) -> None:
        # Bank sync: listen for GUI bank changes if supported.
        try:
            sig = getattr(self._bank_selector, "bank_changed", None)
            if sig is not None:
                sig.connect(self._on_gui_bank_changed)
        except Exception:
            pass

        # Engine state: track active cues + transport state for Play highlighting.
        try:
            eng = self._engine_adapter
            try:
                self._transport_state = str(getattr(eng, "transport_state", self._transport_state) or self._transport_state)
            except Exception:
                pass

            sig = getattr(eng, "cue_started", None)
            if sig is not None:
                sig.connect(self._on_engine_cue_started)
            sig = getattr(eng, "cue_finished", None)
            if sig is not None:
                sig.connect(self._on_engine_cue_finished)
            sig = getattr(eng, "transport_state_changed", None)
            if sig is not None:
                sig.connect(self._on_engine_transport_state_changed)
        except Exception:
            pass

        # Button state changes: mark dirty so labels refresh quickly.
        try:
            bank_widgets = getattr(self._bank_selector, "_bank_widgets", None) or []
            for bank in bank_widgets:
                for btn in getattr(bank, "buttons", []) or []:
                    try:
                        btn.state_changed.connect(self._on_any_button_state_changed)
                    except Exception:
                        continue
        except Exception:
            pass

    def _sync_display_bank_from_gui(self, *, initial: bool) -> None:
        if self._mode != self.BankMode.SYNC:
            return
        idx = 0
        try:
            idx_fn: Optional[Callable[[], int]] = getattr(self._bank_selector, "current_bank_index", None)
            if callable(idx_fn):
                idx = int(idx_fn())
            else:
                idx = int(getattr(self._bank_selector, "_current_bank_index", 0))
        except Exception:
            idx = 0
        self._display_bank_index = max(0, idx)
        if not initial:
            self._force_full_redraw = True

    @Slot(int)
    def _on_gui_bank_changed(self, index: int) -> None:
        if self._mode != self.BankMode.SYNC:
            return
        try:
            self._display_bank_index = int(index)
        except Exception:
            self._display_bank_index = 0
        self._force_full_redraw = True

    @Slot(object)
    def _on_any_button_state_changed(self, _payload: object) -> None:
        # Only redraw if this button is on the currently displayed bank.
        try:
            btn = self.sender()
        except Exception:
            btn = None
        if btn is None:
            return

        try:
            bank_idx = int(getattr(btn, "bank_index", -1))
        except Exception:
            bank_idx = -1

        if bank_idx != int(self._display_bank_index):
            return

        try:
            idx_in_bank = int(getattr(btn, "index_in_bank", 0))
        except Exception:
            idx_in_bank = 0
        if not (1 <= idx_in_bank <= 24):
            return

        key = idx_in_bank - 1
        self._dirty_keys.add(key)

    @Slot(str, object)
    def _on_engine_cue_started(self, cue_id: str, _cue_info: object) -> None:
        try:
            cid = str(cue_id or "")
            if cid:
                self._active_cue_ids.add(cid)
        except Exception:
            pass

        # A cue starting implies the engine is in an audible/playing state.
        # (EngineAdapter.transport_state may be stale because play_cue() does not
        # necessarily update it.)
        self._transport_state = "playing"
        if self._transport_selected_key in (27, 28):
            self._transport_selected_key = None
        self._force_full_redraw = True
        self._render_transport_now()

    @Slot(str, object, str)
    def _on_engine_cue_finished(self, cue_id: str, _cue_info: object, _reason: str) -> None:
        try:
            cid = str(cue_id or "")
            if cid:
                self._active_cue_ids.discard(cid)
        except Exception:
            pass
        self._force_full_redraw = True
        self._render_transport_now()

    @Slot(str)
    def _on_engine_transport_state_changed(self, state: str) -> None:
        try:
            self._transport_state = str(state or self._transport_state)
        except Exception:
            pass

        normalized = str(self._transport_state).lower().strip()
        if normalized == "playing":
            if self._transport_selected_key in (27, 28):
                self._transport_selected_key = None
        elif normalized == "paused":
            # If the engine pauses outside of Stream Deck input, reflect it.
            self._transport_selected_key = 27
        elif normalized == "stopped":
            # If the engine stops outside of Stream Deck input, reflect it.
            self._transport_selected_key = 28
            try:
                self._active_cue_ids.clear()
            except Exception:
                pass
        self._force_full_redraw = True
        self._render_transport_now()

    @Slot()
    def _drain_key_events(self) -> None:
        drained = 0
        while drained < 50:
            try:
                key, pressed = self._key_event_q.get_nowait()
            except queue.Empty:
                break

            drained += 1
            if not pressed:
                continue  # only on press

            self._handle_key_press(int(key))

    def _handle_key_press(self, key: int) -> None:
        # Cue grid keys
        if 0 <= key <= 23:
            idx_in_bank = key + 1
            btn = self._get_button(self._display_bank_index, idx_in_bank)
            if btn is None:
                return

            try:
                file_path = getattr(btn, "file_path", None)
                if not file_path:
                    return

                is_playing = bool(getattr(btn, "is_playing", False))
                auto_fade = bool(getattr(btn, "auto_fade_enabled", False))
                if is_playing and auto_fade:
                    cue_id = getattr(btn, "current_cue_id", "") or ""
                    fade_out_ms = int(getattr(btn, "fade_out_ms", 0) or 0)
                    btn.request_stop.emit(cue_id, fade_out_ms)
                else:
                    # Uses the same play signal path as a GUI click.
                    btn._request_play()

                # Ensure the key gets refreshed promptly.
                self._dirty_keys.add(key)
            except Exception:
                return
            return

        # Bottom row controls
        if key == 24:
            self._bank_nav(-1)
            return
        if key == 25:
            self._bank_nav(+1)
            return
        if key == 26:
            try:
                self._engine_adapter.transport_play()
            except Exception:
                pass
            self._transport_selected_key = 26
            self._force_full_redraw = True
            self._render_transport_now()
            return
        if key == 27:
            try:
                self._engine_adapter.transport_pause()
            except Exception:
                pass
            self._transport_selected_key = 27
            self._force_full_redraw = True
            self._render_transport_now()
            return
        if key == 28:
            try:
                self._engine_adapter.transport_stop()
            except Exception:
                pass
            # Ensure Stream Deck state reflects that nothing is active.
            try:
                self._active_cue_ids.clear()
            except Exception:
                pass
            self._transport_state = "stopped"
            self._transport_selected_key = 28
            self._force_full_redraw = True
            self._render_transport_now()
            return
        if key == 29:
            try:
                self._bank_selector.transport_next()
            except Exception:
                pass
            return

        if key == 30:
            # Loop: toggle loop button and also toggle loop override checkbox.
            pc = self._play_controls
            if pc is None:
                return
            try:
                loop_btn = getattr(pc, "loop_button", None)
                override_chk = getattr(pc, "loop_overide_checkbox", None)
                if loop_btn is None or override_chk is None:
                    return
                new_state = not bool(loop_btn.isChecked())
                loop_btn.setChecked(bool(new_state))
                override_chk.setChecked(bool(new_state))
                self._force_full_redraw = True
            except Exception:
                return
            return

        if key == 31:
            # GUI Sync toggle: switch between SYNC and INDEPENDENT modes.
            try:
                if self._mode == self.BankMode.SYNC:
                    self.set_mode(self.BankMode.INDEPENDENT)
                else:
                    self.set_mode(self.BankMode.SYNC)
                self._force_full_redraw = True
            except Exception:
                return
            return

    def _bank_nav(self, delta: int) -> None:
        try:
            banks = int(getattr(self._bank_selector, "banks", 10))
        except Exception:
            banks = 10

        new_idx = int(self._display_bank_index) + int(delta)
        new_idx = max(0, min(banks - 1, new_idx))

        if self._mode == self.BankMode.SYNC:
            # Drive GUI; we'll follow via bank_changed.
            try:
                self._bank_selector.set_current_bank(int(new_idx))
            except Exception:
                self._display_bank_index = int(new_idx)
        else:
            self._display_bank_index = int(new_idx)

        self._force_full_redraw = True

    def _render_transport_now(self) -> None:
        """Immediately refresh Play/Pause/Stop visuals for snappy feedback."""
        if self._deck is None:
            return

        play_should_highlight = self._play_should_highlight()
        try:
            state = str(getattr(self._engine_adapter, "transport_state", self._transport_state) or self._transport_state)
        except Exception:
            state = self._transport_state
        state = (state or "").lower().strip()

        pause_selected = ((state == "paused") or (self._transport_selected_key == 27)) and (not play_should_highlight)
        stop_selected = ((state == "stopped") or (self._transport_selected_key == 28)) and (not play_should_highlight)
        if state == "paused":
            stop_selected = False
        if state == "stopped":
            pause_selected = False

        # Use separate inactive vs active colors so "active" looks brighter AND
        # more saturated (the generic "lift" adds equally to RGB and can wash out
        # saturation on red/green keys).
        play_inactive = (25, 70, 25)
        play_active = (0, 165, 0)
        pause_inactive = (70, 70, 70)
        pause_active = (165, 165, 165)
        stop_inactive = (80, 30, 30)
        stop_active = (185, 0, 0)

        try:
            self._enqueue_render_priority(
                _KeyRender(
                    key=26,
                    text="",
                    active_level=0.0,
                    icon_path=self._asset_path("play_icon.png"),
                    bg_rgb=play_active if (play_should_highlight or self._transport_selected_key == 26) else play_inactive,
                )
            )
            self._enqueue_render_priority(
                _KeyRender(
                    key=27,
                    text="",
                    active_level=0.0,
                    icon_path=self._asset_path("pause_icon.png"),
                    bg_rgb=pause_active if pause_selected else pause_inactive,
                )
            )
            self._enqueue_render_priority(
                _KeyRender(
                    key=28,
                    text="",
                    active_level=0.0,
                    icon_path=self._asset_path("stop_icon.png"),
                    bg_rgb=stop_active if stop_selected else stop_inactive,
                )
            )
        except Exception:
            return

    def _get_button(self, bank_index: int, idx_in_bank: int):
        try:
            bank_widgets = getattr(self._bank_selector, "_bank_widgets", None) or []
            bank = bank_widgets[int(bank_index)]
            buttons = getattr(bank, "buttons", []) or []
            i = int(idx_in_bank) - 1
            if 0 <= i < len(buttons):
                return buttons[i]
        except Exception:
            return None
        return None

    def _any_cue_playing(self) -> bool:
        """Best-effort: True if any SoundFileButton reports is_playing."""
        try:
            bank_widgets = getattr(self._bank_selector, "_bank_widgets", None) or []
            for bank in bank_widgets:
                for btn in getattr(bank, "buttons", []) or []:
                    try:
                        if bool(getattr(btn, "is_playing", False)):
                            return True
                    except Exception:
                        continue
        except Exception:
            return False
        return False

    def _play_should_highlight(self) -> bool:
        """Play is active when a cue is active and transport isn't paused.

        EngineAdapter.transport_state can lag behind cue-start events (e.g. it may
        still report "stopped" when a new cue has started). In that case, active
        cues are treated as authoritative activity unless the engine is explicitly
        paused.
        """
        try:
            state = str(getattr(self._engine_adapter, "transport_state", self._transport_state) or self._transport_state)
        except Exception:
            state = self._transport_state

        state = (state or "").lower().strip()
        try:
            has_active = bool(self._active_cue_ids)
        except Exception:
            has_active = False

        if state == "paused":
            return False

        # If we have active cues, highlight Play even if state is stale.
        return bool(has_active)

    # ---------------------------------------------------------------------
    # Rendering (Qt thread -> IO thread)
    # ---------------------------------------------------------------------

    @Slot()
    def _render_tick(self) -> None:
        if self._deck is None:
            return

        play_should_highlight = self._play_should_highlight()
        try:
            state = str(getattr(self._engine_adapter, "transport_state", self._transport_state) or self._transport_state)
        except Exception:
            state = self._transport_state
        state = (state or "").lower().strip()

        pause_selected = ((state == "paused") or (self._transport_selected_key == 27)) and (not play_should_highlight)
        stop_selected = ((state == "stopped") or (self._transport_selected_key == 28)) and (not play_should_highlight)
        if state == "paused":
            stop_selected = False
        if state == "stopped":
            pause_selected = False

        # In sync mode, stay aligned even if bank_changed signal wasn't available.
        self._sync_display_bank_from_gui(initial=False)

        now = time.monotonic()
        phase = 0.0
        if self._pulse_period_s > 0:
            phase = (now % self._pulse_period_s) / self._pulse_period_s
        pulse = 0.5 - 0.5 * math.cos(phase * 2.0 * math.pi)  # 0..1

        # Render cue grid (24 keys)
        for key in range(24):
            idx_in_bank = key + 1
            btn = self._get_button(self._display_bank_index, idx_in_bank)
            if btn is None:
                text = ""
                active = False
                bg_rgb = (0, 0, 0)
                fg_rgb = (255, 255, 255)
                corner_text = ""
            else:
                try:
                    file_path = getattr(btn, "file_path", None)
                except Exception:
                    file_path = None
                has_file = bool(file_path)

                try:
                    text = str(btn.text() or "")
                except Exception:
                    text = ""
                try:
                    active = bool(getattr(btn, "is_playing", False))
                except Exception:
                    active = False

                # Match GUI colors from SoundFileButton (best-effort).
                # If no file is assigned, render much darker (nearly black).
                bg_rgb = (10, 10, 10) if not has_file else (60, 60, 60)
                fg_rgb = (255, 255, 255)
                try:
                    bg = getattr(btn, "bg_color", None)
                    if bg is not None:
                        # GUI buttons default to bg_color=gray (#808080) but are styled
                        # to appear darker; mimic that for assigned-file cues.
                        bg_name = ""
                        try:
                            bg_name = str(bg.name() or "").lower()
                        except Exception:
                            bg_name = ""
                        if (not has_file) and bg_name == "#808080":
                            bg_rgb = (10, 10, 10)
                        elif has_file and bg_name == "#808080":
                            bg_rgb = (60, 60, 60)
                        else:
                            bg_rgb = (int(bg.red()), int(bg.green()), int(bg.blue()))
                except Exception:
                    pass
                try:
                    fg = getattr(btn, "text_color", None)
                    if fg is not None:
                        fg_rgb = (int(fg.red()), int(fg.green()), int(fg.blue()))
                except Exception:
                    pass

                corner_text = ""
                if self._show_corner_label:
                    try:
                        corner_text = f"{int(self._display_bank_index)}-{int(idx_in_bank)}"
                    except Exception:
                        corner_text = ""

            prev = self._last_snapshot.get(key)
            needs = self._force_full_redraw or (key in self._dirty_keys)

            if active:
                # Active keys need periodic refresh to animate.
                needs = True

            if not needs and prev is not None:
                prev_text, prev_active = prev
                if prev_text != text or prev_active != active:
                    needs = True

            if needs:
                level = float(pulse) if active else 0.0
                self._enqueue_render(
                    _KeyRender(
                        key=key,
                        text=text,
                        active_level=level,
                        bg_rgb=bg_rgb,
                        fg_rgb=fg_rgb,
                        corner_text=corner_text,
                    )
                )
                self._last_snapshot[key] = (text, active)

        # Bottom row labels
        self._enqueue_render(_KeyRender(key=24, text="BANK -", active_level=0.0), force=self._force_full_redraw)
        self._enqueue_render(_KeyRender(key=25, text="BANK +", active_level=0.0), force=self._force_full_redraw)

        transport_force = (
            self._force_full_redraw
            or (self._last_transport_selected_key != self._transport_selected_key)
            or (self._last_play_highlight != bool(play_should_highlight))
        )

        # See _render_transport_now(): choose inactive vs active background colors
        # rather than relying on the generic RGB lift.
        play_inactive = (25, 70, 25)
        play_active = (0, 165, 0)
        pause_inactive = (70, 70, 70)
        pause_active = (165, 165, 165)
        stop_inactive = (80, 30, 30)
        stop_active = (185, 0, 0)

        # Transport keys: use the same icon assets as PlayControls.
        self._enqueue_render(
            _KeyRender(
                key=26,
                text="",
                active_level=0.0,
                icon_path=self._asset_path("play_icon.png"),
                bg_rgb=play_active if (play_should_highlight or self._transport_selected_key == 26) else play_inactive,
            ),
            force=transport_force,
        )
        self._enqueue_render(
            _KeyRender(
                key=27,
                text="",
                active_level=0.0,
                icon_path=self._asset_path("pause_icon.png"),
                bg_rgb=pause_active if pause_selected else pause_inactive,
            ),
            force=transport_force,
        )
        self._enqueue_render(
            _KeyRender(
                key=28,
                text="",
                active_level=0.0,
                icon_path=self._asset_path("stop_icon.png"),
                bg_rgb=stop_active if stop_selected else stop_inactive,
            ),
            force=transport_force,
        )

        self._enqueue_render(_KeyRender(key=29, text="NEXT", active_level=0.0), force=self._force_full_redraw)

        # Key 30: loop state (dynamic)
        loop_on = False
        try:
            pc = self._play_controls
            if pc is not None:
                loop_btn = getattr(pc, "loop_button", None)
                if loop_btn is not None:
                    loop_on = bool(loop_btn.isChecked())
        except Exception:
            loop_on = False
        # Loop key uses icon (brightness indicates on/off).
        loop_text = ""
        prev = self._last_snapshot.get(30)
        if self._force_full_redraw or prev is None or prev[0] != loop_text or prev[1] != loop_on:
            self._enqueue_render(
                _KeyRender(
                    key=30,
                    text=loop_text,
                    active_level=0.6 if loop_on else 0.0,
                    icon_path=self._asset_path("loop_icon.png"),
                    bg_rgb=(25, 25, 25),
                )
            )
            self._last_snapshot[30] = (loop_text, loop_on)

        # Key 31: gui sync mode (dynamic)
        sync_on = self._mode == self.BankMode.SYNC
        sync_text = "GUI\nSYNC" if sync_on else "INDEP"
        prev = self._last_snapshot.get(31)
        if self._force_full_redraw or prev is None or prev[0] != sync_text:
            self._enqueue_render(_KeyRender(key=31, text=sync_text, active_level=0.6 if sync_on else 0.0))
            self._last_snapshot[31] = (sync_text, sync_on)

        self._force_full_redraw = False
        self._dirty_keys.clear()
        self._last_transport_selected_key = self._transport_selected_key
        self._last_play_highlight = bool(play_should_highlight)

    def _enqueue_render(self, item: _KeyRender, *, force: bool = True) -> None:
        if not force:
            # For static labels, only enqueue when full redraw requested.
            return
        try:
            self._io_q.put_nowait(item)
        except Exception:
            # Drop frames if IO thread is backlogged.
            return

    def _enqueue_render_priority(self, item: _KeyRender) -> None:
        """Enqueue a render and make room if the queue is full."""
        try:
            self._io_q.put_nowait(item)
            return
        except Exception:
            pass

        dropped = 0
        while dropped < 64:
            try:
                _ = self._io_q.get_nowait()
                dropped += 1
            except Exception:
                break

        try:
            self._io_q.put_nowait(item)
        except Exception:
            return

    def _asset_path(self, filename: str) -> Optional[str]:
        try:
            p = os.path.join(self._assets_dir, filename)
            return p if os.path.exists(p) else None
        except Exception:
            return None

    # ---------------------------------------------------------------------
    # IO thread: render PIL + write to hardware
    # ---------------------------------------------------------------------

    def _io_loop(self) -> None:
        icon_cache = {}
        font_cache = {}
        while not self._io_stop.is_set():
            try:
                item = self._io_q.get(timeout=0.25)
            except queue.Empty:
                continue

            if item.key < 0:
                continue

            deck = self._deck
            if deck is None:
                continue

            try:
                img = self._render_key_image(
                    item.text,
                    item.active_level,
                    item.icon_path,
                    item.bg_rgb,
                    item.fg_rgb,
                    item.corner_text,
                    icon_cache,
                    font_cache,
                )
                native = self._to_native(deck, img)
                deck.set_key_image(int(item.key), native)
            except Exception:
                continue

    def _render_key_image(
        self,
        text: str,
        active_level: float,
        icon_path: Optional[str],
        bg_rgb: Optional[tuple[int, int, int]],
        fg_rgb: Optional[tuple[int, int, int]],
        corner_text: str,
        icon_cache: dict,
        font_cache: dict,
    ):
        from PIL import Image, ImageDraw, ImageFont

        w, h = self._key_size
        w = int(w)
        h = int(h)

        level = float(max(0.0, min(1.0, active_level)))
        fg = (255, 255, 255)
        if fg_rgb is not None:
            try:
                fg = (int(fg_rgb[0]), int(fg_rgb[1]), int(fg_rgb[2]))
            except Exception:
                fg = (255, 255, 255)

        if bg_rgb is None:
            # Monochrome pulse: black -> light gray.
            bg = int(20 + level * 140)
            base = (bg, bg, bg)
        else:
            # Optional highlight lift when active_level > 0.
            r, g, b = (int(bg_rgb[0]), int(bg_rgb[1]), int(bg_rgb[2]))
            lift = int(60 * level)
            base = (min(255, r + lift), min(255, g + lift), min(255, b + lift))

        img = Image.new("RGB", (w, h), base)
        draw = ImageDraw.Draw(img)

        # If an icon is provided, render it centered and skip text.
        if icon_path:
            try:
                icon = icon_cache.get(icon_path)
                if icon is None:
                    icon = Image.open(icon_path).convert("RGBA")
                    icon_cache[icon_path] = icon

                # Scale icon to fit with padding.
                pad = 8
                max_w = max(1, w - pad * 2)
                max_h = max(1, h - pad * 2)
                scale = min(max_w / icon.width, max_h / icon.height)
                new_size = (max(1, int(icon.width * scale)), max(1, int(icon.height * scale)))
                icon_resized = icon.resize(new_size)

                x = (w - icon_resized.width) // 2
                y = (h - icon_resized.height) // 2
                img_rgba = img.convert("RGBA")
                img_rgba.alpha_composite(icon_resized, (int(x), int(y)))
                return img_rgba.convert("RGB")
            except Exception:
                # Fall back to text rendering.
                pass

        # Use default font to avoid platform font dependencies.
        def _load_font(size: int):
            size = int(size)
            cached = font_cache.get(size)
            if cached is not None:
                return cached

            # Prefer fonts that typically exist with Pillow or on Windows.
            for name in ("DejaVuSans.ttf", "arial.ttf", "segoeui.ttf"):
                try:
                    f = ImageFont.truetype(name, size)
                    font_cache[size] = f
                    return f
                except Exception:
                    continue

            try:
                f = ImageFont.load_default()
                font_cache[size] = f
                return f
            except Exception:
                return None

        def _text_bbox(s: str, font_obj) -> tuple[int, int]:
            try:
                b = draw.textbbox((0, 0), s, font=font_obj)
                return int(b[2] - b[0]), int(b[3] - b[1])
            except Exception:
                # Fallback rough estimate.
                return max(0, len(s) * 6), 10

        def _split_long_word(word: str, font_obj, max_w: int) -> list[str]:
            if not word:
                return [""]
            parts: list[str] = []
            remaining = word
            # Greedy split by characters, trying to keep chunks as large as possible.
            while remaining:
                lo, hi = 1, len(remaining)
                best = 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    chunk = remaining[:mid]
                    w0, _h0 = _text_bbox(chunk, font_obj)
                    if w0 <= max_w:
                        best = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1
                parts.append(remaining[:best])
                remaining = remaining[best:]
            return parts

        def _wrap_lines(font_obj, max_w: int, max_lines: int) -> list[str]:
            raw = (text or "").strip()
            if not raw:
                return []

            # Respect explicit newlines first.
            if "\n" in raw:
                chunks: list[str] = []
                for ln in raw.splitlines():
                    ln = " ".join((ln or "").strip().split())
                    if ln:
                        chunks.append(ln)
                raw_words = []
                for ln in chunks:
                    raw_words.append((ln, True))
            else:
                raw = " ".join(raw.split())
                raw_words = [(w, False) for w in raw.split(" ") if w]

            lines: list[str] = []
            current = ""

            for w, is_hard_break in raw_words:
                if is_hard_break:
                    # Start a new line with this segment (but allow wrapping inside it).
                    segment_words = w.split(" ")
                else:
                    segment_words = [w]

                for seg in segment_words:
                    # Split long words to avoid overflow.
                    seg_parts = _split_long_word(seg, font_obj, max_w)
                    for part in seg_parts:
                        candidate = (current + " " + part).strip() if current else part
                        cw, _ch = _text_bbox(candidate, font_obj)
                        if cw <= max_w:
                            current = candidate
                        else:
                            if current:
                                lines.append(current)
                            current = part
                            if len(lines) >= max_lines:
                                return lines[:max_lines]

                if is_hard_break:
                    if current:
                        lines.append(current)
                        current = ""
                        if len(lines) >= max_lines:
                            return lines[:max_lines]

            if current:
                lines.append(current)
            return lines[:max_lines]

        # Corner label geometry (user-adjustable; keep in sync with draw below)
        corner_font_size = 14
        corner_pos = (20, 0)

        # Choose the largest font that fits within the key.
        pad = 6
        max_w = max(1, w - pad * 2)
        # If we're drawing a corner label at the top, reserve vertical space so
        # the main label never overlaps it.
        top_reserved = 0
        if corner_text:
            try:
                corner_font = _load_font(corner_font_size)
                if corner_font is not None:
                    _cw, _ch = _text_bbox(str(corner_text), corner_font)
                    top_reserved = int(_ch) + 2
            except Exception:
                top_reserved = 0

        content_y0 = pad + max(0, top_reserved)
        content_y1 = h - pad
        max_h = max(1, content_y1 - content_y0)
        max_lines = 4

        best_font = None
        best_lines: list[str] = []
        for size in range(28, 7, -1):
            f = _load_font(size)
            if f is None:
                continue
            lines = _wrap_lines(f, max_w=max_w, max_lines=max_lines)
            if not lines and not (text or "").strip():
                best_font = f
                best_lines = []
                break

            widths = []
            heights = []
            for ln in lines:
                lw, lh = _text_bbox(ln, f)
                widths.append(lw)
                heights.append(lh)

            if not widths:
                continue

            total_h = sum(heights) + max(0, (len(lines) - 1) * 2)
            if max(widths) <= max_w and total_h <= max_h:
                best_font = f
                best_lines = lines
                break

        # Fall back if fitting fails.
        if best_font is None:
            best_font = _load_font(10)
            best_lines = _wrap_lines(best_font, max_w=max_w, max_lines=max_lines) if best_font else []

        lines = best_lines

        # Vertical center.
        y = content_y0
        if lines and best_font is not None:
            heights = [_text_bbox(ln, best_font)[1] for ln in lines]
            total_h = sum(heights) + max(0, (len(lines) - 1) * 2)
            y = max(content_y0, content_y0 + (max_h - total_h) // 2)

        for i, ln in enumerate(lines):
            if best_font is not None:
                tw, th = _text_bbox(ln, best_font)
            else:
                tw, th = (len(ln) * 6, 10)
            x = max(pad, int((w - tw) // 2))
            draw.text((x, y), ln, fill=fg, font=best_font)
            y += th + 2

        # Optional small top-left overlay (bank-index/button-index)
        if corner_text:
            try:
                corner_font = _load_font(corner_font_size)
                if corner_font is not None:
                    draw.text(corner_pos, str(corner_text), fill=fg, font=corner_font)
            except Exception:
                pass

        return img

    def _to_native(self, deck, pil_img):
        try:
            from StreamDeck.ImageHelpers import PILHelper
        except Exception:
            return None
        try:
            return PILHelper.to_native_format(deck, pil_img)
        except Exception:
            return None
