"""
SoundFileButton: GUI-Only Audio Clip Button Widget

A stateful button widget that manages a single audio clip's UI state and lifecycle.
This widget is completely decoupled from the audio engine via the EngineAdapter.

Responsibilities:
- Display clip metadata (filename, duration, status)
- Validate and probe audio files
- Maintain per-clip state (in/out points, loop flag, gain, etc.)
- Display real-time playback telemetry (time remaining, levels)
- Emit signals for playback intent (play, stop, fade)
- React to engine events via subscribed signals (cue_started, cue_finished, cue_time, cue_levels)

Signals Emitted (to MainWindow / EngineAdapter):
- request_play: Playback request with file path and parameters
- request_stop: Stop request with fade duration
- request_fade: Fade request with target gain and duration

Signals Subscribed:
- (from EngineAdapter)
  - cue_started: Button becomes active
  - cue_finished: Button becomes inactive
  - cue_time: Update time display
  - cue_levels: Update level meters

Architecture:
- No AudioEngine imports
- No engine process/service imports
- No blocking calls
- All communication via Qt signals and EngineAdapter
- Maintains local CueInfo template for this button
- Generates unique cue_id on each play request
"""

from __future__ import annotations

import statistics
import time
import uuid
from typing import Optional, TYPE_CHECKING, Callable
import threading

import numpy as np

from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QPushButton, QFileDialog, QMenu, QDialog, QVBoxLayout, QLabel, QSlider, QSpinBox, QColorDialog, QHBoxLayout, QSizePolicy, QWidget, QMessageBox
from PySide6.QtCore import Signal, QTimer, Qt, QTime, QPoint, QRect, QPointF, QEvent, QPropertyAnimation, QEasingCurve, QThread, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QRadialGradient, QBrush, QPolygon, QResizeEvent, QDrag
from PySide6.QtCore import QMimeData

from engine.cue import Cue, CueInfo
from ui.widgets.AudioLevelMeter import AudioLevelMeter

if TYPE_CHECKING:
    from gui.engine_adapter import EngineAdapter


class FadeButton(QPushButton):
    """Custom fade button with striped visualization."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet('border:3px solid black;')
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        
        bkgd = QColor(127, 127, 127, 127)
        color = QColor(0, 0, 127, 127)

        painter.fillRect(self.rect(), bkgd)
        
        bars = 4
        
        for i in range(bars):
            h_step = int(self.height() / bars)
            w_bar = int(self.width() / (bars * 2))
            h = self.height()
            angle = int(self.height() / bars)
            
            ii = i * 2
            tl = QPoint(0 + (ii * w_bar), 0 + i * h_step)
            tr = QPoint(w_bar + (ii * w_bar), angle + i * h_step)
            br = QPoint(w_bar + (ii * w_bar), h)
            bl = QPoint(0 + (ii * w_bar), h) 
            
            bar_1 = QPolygon([tl, tr, br, bl])
        
            painter.setBrush(color) 
            painter.drawPolygon(bar_1)
        
        painter.end()


class SoundFileButton(QPushButton):
    """
    GUI-Only Audio Clip Button.
    
    This button represents a single audio clip/cue that the user can interact with.
    It maintains no direct connection to the audio engine; all playback requests are
    sent via signals to be routed through EngineAdapter.
    
    State:
    - file_path: Path to the audio file
    - is_playing: Whether this cue is currently playing
    - current_cue_id: The cue_id from the last play request (used to correlate events)
    - Metadata: duration, sample_rate, channels (from file probe)
    - Clip parameters: in_frame, out_frame, loop_enabled, gain_db
    - Telemetry: elapsed_seconds, remaining_seconds, rms_level, peak_level
    """

    # ==========================================================================
    # SIGNALS (emitted to MainWindow / routed to EngineAdapter)
    # ==========================================================================

    request_play = Signal(str, dict)  # file_path: str, params: dict
    """
    Emitted when user clicks button to start playback.
    
    Args:
        file_path (str): Absolute path to audio file
        params (dict): Playback parameters including:
            - track_id, gain_db, in_frame, out_frame, fade_in_ms, layered, total_seconds
    """

    request_stop = Signal(str, int)  # cue_id: str, fade_out_ms: int
    """
    Emitted when user requests stop (e.g., fade-out button).
    
    Args:
        cue_id (str): Identifier of the cue to stop
        fade_out_ms (int): Fade-out duration in milliseconds
    """

    request_fade = Signal(str, float, int)  # cue_id: str, target_db: float, duration_ms: int
    """
    Emitted when user requests a fade (e.g., volume slider).
    
    Args:
        cue_id (str): Identifier of the cue
        target_db (float): Target gain in dB
        duration_ms (int): Fade duration in milliseconds
    """
    
    update_cue_settings = Signal(str, CueInfo)  # cue_id: str, cue_info: CueInfo

    # Class variables for drag control
    _dragging_button: Optional[SoundFileButton] = None
    drag_enabled: bool = True  # Global toggle for drag and drop functionality
    gesture_enabled: bool = True  # Global toggle for swipe gestures

    # ==========================================================================
    # CONSTRUCTOR & INITIALIZATION
    # ==========================================================================

    def __init__(
        self,
        label: str = "Empty",
        file_path: Optional[str] = None,
        engine_adapter: Optional[EngineAdapter] = None,
    ) -> None:
        """
        Initialize the button widget.
        
        Args:
            label (str): Initial button text
            file_path (str or None): Path to audio file (optional)
            engine_adapter (EngineAdapter or None): Reference to the engine adapter
                                                     for subscribing to signals
        """
        super().__init__(label)
        
        self.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.MinimumExpanding
        )
        self.engine_adapter = engine_adapter
        
        # File and metadata
        self.file_path: Optional[str] = file_path
        self.duration_seconds: Optional[float] = None
        self.sample_rate: Optional[int] = None
        self.channels: Optional[int] = None
        self.song_title: Optional[str] = None  # Extracted from file metadata
        self.song_artist: Optional[str] = None  # Extracted from file metadata
        
        # Drag and drop tracking
        self._drag_start_pos: Optional[QPoint] = None
        
        # Gain slider gesture state
        self.gain_slider_visible: bool = False
        self.gain_slider: Optional[QSlider] = None
        self.gain_label: Optional[QLabel] = None
        self.slider_animation: Optional[QPropertyAnimation] = None
        self._swipe_start_pos: Optional[QPoint] = None  # Track swipe start position
        self._swipe_start_time: Optional[float] = None  # Track swipe timing
        
        # Audio level meters for channels (displayed with gain slider)
        self.level_meter_left: Optional[AudioLevelMeter] = None
        self.level_meter_right: Optional[AudioLevelMeter] = None
        self.meters_animation: Optional[QPropertyAnimation] = None
        self.meters_should_update: bool = False  # Track if meters should receive level updates (independent of visibility)
        
        # Playback state
        self.is_playing: bool = False
        self.current_cue_id: Optional[str] = None
        self._active_cue_ids: set[str] = set()  # Track all cue_ids created by this button
        self._adapter_subscribed: bool = False  # Track if we've subscribed to avoid double-subscription
        
        # Clip parameters (per-button settings)
        self.in_frame: int = 0
        self.out_frame: Optional[int] = None
        self.loop_enabled: bool = False
        self.gain_db: float = 0.0
        self.auto_fade_enabled: bool = False  # If True: fade old cue before new one. If False: layered playback
        self._cleanup_dispatcher: Optional[Callable[["SoundFileButton"], None]] = None
        
        # Fade durations (configurable, will come from settings window)
        self.fade_in_ms: int = 100  # Fade-in duration in milliseconds
        self.fade_out_ms: int = 500  # Fade-out duration in milliseconds
        
        # Real-time telemetry
        self.elapsed_seconds: float = 0.0
        self.remaining_seconds: float = 0.0
        self.rms_level: float = 0.0
        self.peak_level: float = 0.0
        self.light_level: float = 1.0  # For playing indicator gradient (0-255 scale)
        
        # Flashing effect during playback (subtle pulse animation)
        self.flash_anim: Optional[QVariantAnimation] = None
        self._flash_base_color: QColor = QColor("#70CC70")
        
        # Custom colors
        self.bg_color: Optional[QColor] = None  # Custom background color (overrides flash)
        self.text_color: Optional[QColor] = None  # Custom text/font color
        
        # Create fade button (will be sized and positioned in resizeEvent)
        self.fade_button = FadeButton()
        self.fade_button.setParent(self)
        self.fade_button.setDisabled(True)
        self.fade_button.hide()
        self.fade_button.released.connect(self._fade_out)
        
        # UI setup
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.clicked.connect(self._on_click)
        
        # Enable drag and drop
        self.setAcceptDrops(True)
        
        # Create gain slider and label (hidden initially, positioned on right side)
        self._setup_gain_slider()
        
        # Probe file if provided
        if file_path:
            self._probe_file_async(file_path)
        
        self._refresh_label()
        
        self.setStyleSheet("background-color: dark gray; ")
        self.bg_color = QColor("gray")
        
        # Note: Engine adapter subscription is handled by ButtonBankWidget for efficient event routing
        # Buttons no longer subscribe directly to avoid broadcasting events to all buttons
    
    def subscribe_to_adapter(self, engine_adapter: EngineAdapter) -> None:
        """
        Subscribe this button to engine adapter signals for real-time updates.
        
        Args:
            engine_adapter (EngineAdapter): The engine adapter instance
        """
        if self._adapter_subscribed:
            return  # Already subscribed, avoid double-subscription
        self._adapter_subscribed = True
        self._subscribe_to_adapter(engine_adapter)
    
    def set_fade_in_duration(self, ms: int) -> None:
        """
        Set fade-in duration in milliseconds.
        
        Args:
            ms (int): Fade-in duration in milliseconds (will be used on next playback)
        """
        self.fade_in_ms = max(0, ms)
    
    def set_fade_out_duration(self, ms: int) -> None:
        """
        Set fade-out duration in milliseconds.
        
        Args:
            ms (int): Fade-out duration in milliseconds (will be used on next transition)
        """
        self.fade_out_ms = max(0, ms)
    
    def get_fade_in_duration(self) -> int:
        """Get current fade-in duration in milliseconds."""
        return self.fade_in_ms
    
    def get_fade_out_duration(self) -> int:
        """Get current fade-out duration in milliseconds."""
        return self.fade_out_ms
    
    def _subscribe_to_adapter(self, adapter: EngineAdapter) -> None:
        """Internal method to connect adapter signals for real-time updates."""
        adapter.cue_started.connect(self._on_cue_started)
        adapter.cue_finished.connect(self._on_cue_finished)
        adapter.cue_time.connect(self._on_cue_time)
        adapter.cue_levels.connect(self._on_cue_levels)
        self.update_cue_settings.connect(adapter.update_cue)
        

    # ==========================================================================
    # PAINTING & RENDERING
    # ==========================================================================
    
    def paintEvent(self, event) -> None:
        """Custom paint event showing playing indicator and button metadata."""
        super().paintEvent(event)
        painter = QPainter(self)

        height = painter.device().height()
        width = painter.device().width()
        
        # Draw playing indicator (circular gradient)
        rect = QRect(0, 0, 15, 15)
        rect.moveTo(QPoint(int(width / 2) - 7, 4))
        
        ctr = QPointF((width / 2), 12)
        
        if self.is_playing:
            gradient = QRadialGradient(ctr, 12, ctr) 
            gradient.setColorAt(0, QColor(255, 255, 255)) 
            gradient.setColorAt(1, QColor(0, int(self.light_level), 0))
        else:
            gradient = QRadialGradient(ctr, 4, ctr) 
            gradient.setColorAt(0, QColor(180, 180, 180)) 
            gradient.setColorAt(1, QColor(0, 0, 0))
              
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(rect)

        # Draw time remaining
        painter.setOpacity(1.0)
        font = QFont("Arial", 12)
        painter.setFont(font)
        painter.setPen(self.palette().color(self.foregroundRole()))
        
        
        if self.remaining_seconds >= 0:
            pos_w = self.width() - 45
            pos_h = 16
            painter.drawText(pos_w, pos_h, self._format_duration(self.remaining_seconds))

        # Draw loop indicator
        if self.loop_enabled:
            width = painter.device().width()
            painter.drawText((width - 40), height - 5, 'LOOP')
        
        painter.end()

    # ==========================================================================
    # UI UPDATES
    # ==========================================================================

    def _refresh_label(self) -> None:
        """
        Update button text and styling for MANUAL CHANGES (file selection, color changes).
        
        Text wraps within the button; button size is not affected by text length.
        """
        if not self.file_path:
            self.setText(self._auto_wrap_text("Choose Sound"))
            self.setStyleSheet("")
            return
        
        # Build display text: prefer song title from metadata, fall back to filename
        display_name = self.song_title or self.file_path.split("/")[-1].split("\\")[-1]
        
        # Add duration if available
        if self.duration_seconds:
            display_text = display_name
            self.remaining_seconds = self.duration_seconds
        else:
            display_text = display_name
        
        # Add playing indicator if active
        if self.is_playing:
            self._start_flash()
        else:
            # Apply custom colors if set, otherwise reset
            if self.bg_color or self.text_color:
                self._apply_stylesheet()
            else:
                self.setStyleSheet("")
            # Stop flashing if it was running
            self._stop_flash()
        
        self.setText(self._auto_wrap_text(display_text))
    
    def _wrap_text_html(self, text: str) -> str:
        """Wrap text in HTML with white-space: normal for button text wrapping."""
        escaped_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<p style='white-space: normal; margin: 0;'>{escaped_text}</p>"
    
    def _auto_wrap_text(self, text: str) -> str:
        """
        Automatically wrap text with newline characters based on button width and font metrics.
        Also optimizes font size to use the largest size that fits within the button.
        Breaks at word boundaries when possible, and at character level for long text without spaces.
        """
        if not text or self.width() < 50 or self.height() < 50:
            return text
        
        # Try different font sizes from large to small, find the largest that fits
        optimal_font_size = 10  # Default
        optimal_lines = []
        
        for font_size in range(14, 6, -1):
            # Create font with this size and test wrapping
            test_font = QFont(self.font())
            test_font.setPointSize(font_size)
            metrics = QFontMetrics(test_font)
            
            # Calculate available width (with padding)
            available_width = self.width() - 20
            available_height = self.height() - 10
            
            words = text.split()
            if not words:
                return text
            
            lines = []
            current_line = []
            
            for word in words:
                # Check if word has spaces or is a single long word
                if ' ' not in word:
                    # Word without spaces - may need character-level wrapping
                    wrapped_word = self._wrap_long_word(word, metrics, available_width)
                    
                    # If wrapping was needed, add current line first
                    if '\n' in wrapped_word:
                        if current_line:
                            lines.append(" ".join(current_line))
                            current_line = []
                        lines.extend(wrapped_word.split('\n'))
                    else:
                        # Check if this word fits on current line
                        test_line = " ".join(current_line + [wrapped_word])
                        line_width = metrics.horizontalAdvance(test_line)
                        
                        if line_width <= available_width:
                            current_line.append(wrapped_word)
                        else:
                            # Word doesn't fit on current line
                            if current_line:
                                lines.append(" ".join(current_line))
                                current_line = [wrapped_word]
                            else:
                                lines.append(wrapped_word)
                else:
                    # Word with spaces - use original logic
                    test_line = " ".join(current_line + [word])
                    line_width = metrics.horizontalAdvance(test_line)
                    
                    if line_width <= available_width:
                        # Word fits, add it to current line
                        current_line.append(word)
                    else:
                        # Word doesn't fit
                        if current_line:
                            # Save current line and start new one with this word
                            lines.append(" ".join(current_line))
                            current_line = [word]
                        else:
                            # Word is too long for a line by itself, just put it on its own line
                            lines.append(word)
            
            # Add any remaining words
            if current_line:
                lines.append(" ".join(current_line))
            
            # Check if this font size fits vertically
            line_height = metrics.lineSpacing()
            total_height = line_height * len(lines)
            
            if total_height <= available_height:
                # This font size works! Use it
                optimal_font_size = font_size
                optimal_lines = lines
                break
        
        # If we didn't find a good size, use the last one we calculated
        if not optimal_lines:
            optimal_lines = lines
        
        # Set the optimal font size
        self._set_font_size(optimal_font_size)
        
        return "\n".join(optimal_lines)

    def _wrap_long_word(self, word: str, metrics: QFontMetrics, available_width: int) -> str:
        """
        Wrap a long word without spaces by breaking it into multiple lines.
        Returns the word with newline characters inserted at appropriate positions.
        
        Args:
            word (str): The long word without spaces
            metrics (QFontMetrics): Font metrics for width calculation
            available_width (int): Maximum width for a single line
            
        Returns:
            str: The word with newline characters inserted, or original word if it fits
        """
        if metrics.horizontalAdvance(word) <= available_width:
            # Word fits on a single line
            return word
        
        # Word is too long - break it into multiple lines
        lines = []
        current_line = ""
        
        for char in word:
            test_line = current_line + char
            if metrics.horizontalAdvance(test_line) <= available_width:
                # Character fits on current line
                current_line = test_line
            else:
                # Character doesn't fit, start new line
                if current_line:
                    lines.append(current_line)
                current_line = char
        
        # Add any remaining characters
        if current_line:
            lines.append(current_line)
        
        return "\n".join(lines)

    
    def _auto_fit_font(self, text: str) -> None:
        """
        Deprecated: Text now wraps within fixed button size.
        This method is kept for compatibility but does nothing.
        """
        pass
    
    def _set_font_size(self, size: int) -> None:
        """Set button font to specific point size."""
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)
    
    def _start_flash(self) -> None:
        """Start a subtle pulse animation between lighter/darker shades."""
        if self.flash_anim is not None:
            return
        base_color = self.bg_color or QColor("#70CC70")
        self._flash_base_color = base_color
        darker = base_color.darker(115)
        lighter = base_color.lighter(120)
        anim = QVariantAnimation(self)
        anim.setDuration(1200)
        anim.setLoopCount(-1)
        anim.setEasingCurve(QEasingCurve.InOutSine)
        anim.setStartValue(darker)
        anim.setKeyValueAt(0.5, lighter)
        anim.setEndValue(darker)
        anim.valueChanged.connect(self._apply_flash_color)
        anim.start()
        self.flash_anim = anim
    
    def _stop_flash(self) -> None:
        """Stop the flash animation and restore base color."""
        if self.flash_anim is not None:
            self.flash_anim.stop()
            self.flash_anim.deleteLater()
            self.flash_anim = None
        # Restore original color (not flashing variation)
        self._apply_stylesheet()
    
    def _apply_flash_color(self, color: QColor) -> None:
        """Apply the interpolated flash color from the animation."""
        if isinstance(color, QColor):
            self._apply_stylesheet(color)
    
    def _apply_stylesheet(self, bg_color: Optional[QColor] = None) -> None:
        """Apply background and text colors via stylesheet."""
        bg = bg_color or self.bg_color
        text = self.text_color
        
        style = ""
        if bg:
            style += f"background-color: {bg.name()};"
        if text:
            style += f"color: {text.name()};"
        
        self.setStyleSheet(style)
    
    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format duration in seconds as HH:MM:SS or MM:SS."""
        try:
            s = int(round(seconds))
            m, s = divmod(s, 60)
            h, m = divmod(m, 60)
            if h > 0:
                return f"{h}:{m:02d}:{s:02d}"
            return f"{m}:{s:02d}"
        except Exception:
            return "?"
    
    def _update_time_display(self) -> None:
        """Update button text with current playback time."""
        if not self.is_playing:
            return
        remaining_str = self._format_duration(self.remaining_seconds)
        elapsed_str = self._format_duration(self.elapsed_seconds)
        
        # Update tooltip with time info
        self.setToolTip(f"Elapsed: {elapsed_str} | Remaining: {remaining_str}")
        self.update()

    # ==========================================================================
    # FILE MANAGEMENT
    # ==========================================================================

    def _show_context_menu(self, pos) -> None:
        """Display context menu with clip options."""
        menu = QMenu(self)
        
        # File management
        choose_file = menu.addAction("Select Track")
        menu.addSeparator()
        
        # Color customization
        set_bg_color = menu.addAction("Background Color…")
        set_text_color = menu.addAction("Text Color…")
        menu.addSeparator()
        
        # Clip editing
        edit_track = menu.addAction("Edit Track")
        menu.addSeparator()
        
        # Clip parameters
        loop_action = menu.addAction(f"Loop ({self._get_loop_status()})")
        auto_fade_action = menu.addAction(f"Auto-Fade Mode ({self._get_auto_fade_status()})")
        menu.addSeparator()
        
        # Playback control (if playing)
        if self.is_playing:
            stop = menu.addAction("Stop")
            fade_out = menu.addAction("Fade Out…")
            menu.addSeparator()
        
        # Reset and clear
        reset_colors = menu.addAction("Reset Colors")
        clear = menu.addAction("Clear Button")
        
        # Execute menu
        action = menu.exec(self.mapToGlobal(pos))
        
        # Handle actions
        if action == choose_file:
            QTimer.singleShot(0, self._choose_file)
        elif action == set_bg_color:
            self._set_background_color_dialog()
        elif action == set_text_color:
            self._set_text_color_dialog()
        elif action == edit_track:
            self._open_editor()
        elif action == loop_action:
            self.loop_enabled = not self.loop_enabled
            self._update_cue_settings()
            self.setToolTip(f"Loop: {self._get_loop_status()}")
        elif action == auto_fade_action:
            self.auto_fade_enabled = not self.auto_fade_enabled
            self.setToolTip(f"Auto-Fade: {self._get_auto_fade_status()}")
        elif action == reset_colors:
            self._reset_colors()
        elif action == clear:
            self._clear_button()
        elif action == stop and self.is_playing:
            for cue_id in list(self._active_cue_ids):
                self.request_stop.emit(cue_id, 0)
        elif action == fade_out and self.is_playing:
            for cue_id in list(self._active_cue_ids):
                self.request_fade.emit(cue_id, -120.0, 500)
    
    def _get_loop_status(self) -> str:
        """Return human-readable loop status."""
        return "ON" if self.loop_enabled else "OFF"
    
    def _get_auto_fade_status(self) -> str:
        """Return human-readable auto-fade status."""
        return "ON" if self.auto_fade_enabled else "OFF"
    
    def _choose_file(self) -> None:
        """Open file dialog to select an audio file."""
        fp, _ = QFileDialog.getOpenFileName(
            self,
            "Choose audio file",
            "",
            "Audio Files (*.wav *.mp3 *.flac *.aac *.m4a);;All Files (*)"
        )
        if fp:
            self.file_path = fp
            self._probe_file_async(fp)
            self._refresh_label()
    
    def _clear_file(self) -> None:
        """Clear the file path and reset button."""
        self.file_path = None
        self.duration_seconds = None
        self.sample_rate = None
        self.channels = None
        self._refresh_label()
    
    def _clear_button(self) -> None:
        """Clear button completely: reset to blank default state."""
        self.file_path = None
        self.duration_seconds = None
        self.sample_rate = None
        self.channels = None
        self.song_title = None
        self.song_artist = None
        self.in_frame = 0
        self.out_frame = None
        self.loop_enabled = False
        self.auto_fade_enabled = False
        self.gain_db = 0.0
        self.fade_in_ms = 100
        self.fade_out_ms = 500
        self.elapsed_seconds = 0.0
        self.remaining_seconds = 0.0
        self.rms_level = 0.0
        self.peak_level = 0.0
        self.current_cue_id = None
        self.is_playing = False
        self.bg_color = None
        self.text_color = None
        self.setText(self._auto_wrap_text("Choose Sound"))
        self.setStyleSheet("")
    
    def _probe_file_async(self, path: str) -> None:
        """
        Probe audio file for metadata (duration, sample rate, channels, song title, artist).
        
        Runs in a background thread to avoid blocking the GUI.
        Starts a worker thread that probes the file and updates button UI when done.
        """
        # Start probing in a background thread to keep GUI responsive
        probe_thread = threading.Thread(
            target=self._probe_file_in_thread,
            args=(path,),
            daemon=True  # Daemon thread won't prevent app exit
        )
        probe_thread.start()
    
    def _probe_file_in_thread(self, path: str) -> None:
        """
        Worker thread function: probe file and update UI via signals.
        
        Runs in background thread, updates UI safely via direct attribute assignment
        since all we're doing is setting text on the button.
        """
        try:
            duration, sr, ch, title, artist = self._probe_file(path)
            # Update button attributes (thread-safe for simple attribute assignment)
            self.duration_seconds = duration
            self.sample_rate = sr
            self.channels = ch
            self.song_title = title
            self.song_artist = artist
            # Trigger UI refresh on main thread via QTimer
            QTimer.singleShot(0, self._refresh_label)
        except Exception as e:
            print(f"[SoundFileButton._probe_file_in_thread] Error: {e}")
            self.duration_seconds = None
            self.song_title = None
            self.song_artist = None
    
    @staticmethod
    def _probe_file(path: str) -> tuple[Optional[float], Optional[int], Optional[int], Optional[str], Optional[str]]:
        """
        Best-effort probe: try PyAV, fall back to wave for WAV files.
        
        Returns:
            (duration_seconds, sample_rate, channels, song_title, song_artist)
        """
        # Try PyAV (fast metadata read)
        try:
            import av
            container = av.open(path)
            stream = next((s for s in container.streams if s.type == "audio"), None)
            
            total_seconds = None
            sr = None
            ch = None
            title = None
            artist = None
            
            if stream is not None:
                # Extract duration
                if getattr(stream, "duration", None) and getattr(stream, "time_base", None):
                    total_seconds = float(stream.duration * stream.time_base)
                
                # Extract sample rate
                if getattr(stream, "rate", None):
                    sr = int(stream.rate)
                
                # Extract channels
                if getattr(stream, "channels", None):
                    ch = int(stream.channels)
            
            # Extract metadata from container level
            if container.metadata:
                try:
                    metadata_dict = dict(container.metadata)
                    title = metadata_dict.get(b"title", metadata_dict.get("title"))
                    artist = metadata_dict.get(b"artist", metadata_dict.get("artist"))
                    
                    # Decode bytes if necessary
                    if isinstance(title, bytes):
                        title = title.decode("utf-8", errors="replace")
                    if isinstance(artist, bytes):
                        artist = artist.decode("utf-8", errors="replace")
                except Exception:
                    pass
            
            try:
                container.close()
            except Exception:
                pass
            
            return (total_seconds, sr, ch, title, artist)
        
        except Exception:
            pass
        
        # Fallback for WAV via wave module
        try:
            import wave
            with wave.open(path, "rb") as w:
                frames = w.getnframes()
                sr = w.getframerate()
                ch = w.getnchannels()
                return (frames / float(sr) if sr else None, sr, ch, None, None)
        except Exception:
            pass
        
        return (None, None, None, None, None)

    # ==========================================================================
    # CLIP EDITING DIALOGS
    # ==========================================================================

    def _edit_clip_points(self) -> None:
        """Dialog to edit in/out frame points."""
        # TODO: Implement in/out point editor dialog with frame selectors
        # Will allow setting custom in_frame and out_frame for partial playback
        pass
    
    def _set_gain_dialog(self) -> None:
        """Dialog to set clip gain."""
        # TODO: Implement gain dialog with slider (-60dB to +12dB)
        # Updates self.gain_db and emits updated value to engine
        pass
    
    def _set_background_color_dialog(self) -> None:
        """Open color picker dialog for background color."""
        color = QColorDialog.getColor(
            self.bg_color or QColor(255, 255, 255),
            self,
            "Choose background color"
        )
        if color.isValid():
            self.bg_color = color
            self._refresh_label()
    
    def _set_text_color_dialog(self) -> None:
        """Open color picker dialog for text color."""
        color = QColorDialog.getColor(
            self.text_color or QColor(0, 0, 0),
            self,
            "Choose text color"
        )
        if color.isValid():
            self.text_color = color
            self._refresh_label()
    
    def _reset_colors(self) -> None:
        """Reset colors to defaults."""
        self.bg_color = None
        self.text_color = None
        self._refresh_label()
    
    def _open_editor(self) -> None:
        """Open audio editor for this file"""
        # TODO: Integrate with internal audio editor
        if self.file_path:
            pass
        

    # ==========================================================================
    # PLAYBACK CONTROL
    # ==========================================================================
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds as mm:ss string."""
        try:
            total_ms = int(seconds * 1000)
            qtime = QTime(0, 0, 0, 0).addMSecs(total_ms)
            return qtime.toString('mm:ss')
        except Exception:
            return "00:00"
    
    def _fade_out(self) -> None:
        """Emit stop request for all active cues owned by this button."""
        for cue_id in list(self._active_cue_ids):
            self.request_stop.emit(cue_id, self.fade_out_ms)

    def _on_click(self) -> None:
        """
        Handle button click: play or stop depending on playback mode.
        
        - If auto_fade_enabled: Fade out current cue before playing new one (auto-transition)
        - If not auto_fade_enabled: Start new cue without stopping old one (layered playback)
        - If gain slider is visible (from gesture), ignore click to prevent accidental play
        """
        # Ignore clicks while gain slider is visible (user is adjusting gain via swipe gesture)
        if self.gain_slider_visible:
            return
        
        if not self.file_path:
            # No file selected; open dialog
            QTimer.singleShot(0, self._choose_file)
            return
        
        if self.is_playing and self.auto_fade_enabled:
            # In auto-transition mode: fade out the old cue before playing new one
            self.request_stop.emit(self.current_cue_id or "", self.fade_out_ms)
        else:
            # Not playing or in layered mode: start new playback immediately
            self._request_play()
    
    def _request_play(self) -> None:
        """Request playback and own the generated cue_id."""
        # Generate a unique cue_id for this playback session
        cue_id = uuid.uuid4().hex
        self._active_cue_ids.add(cue_id)
        
        params = {
            "cue_id": cue_id,  # Button owns this cue_id
            "track_id": None,
            "gain_db": self.gain_db,
            "in_frame": self.in_frame,
            "out_frame": self.out_frame,
            "fade_in_ms": self.fade_in_ms,
            "loop_enabled": self.loop_enabled,
            "layered": True,
            "total_seconds": self.duration_seconds,
        }
        
        self.request_play.emit(self.file_path, params)
        
    def _update_cue_settings(self) -> None:
        """Emit updated cue settings to engine for the current cue."""
        if not self.current_cue_id:
            print(f"[SoundFileButton._update_cue_settings] Skipping - no current_cue_id")
            return
        print(f"[SoundFileButton._update_cue_settings] Emitting signal for cue_id: {self.current_cue_id} with gain_db: {self.gain_db}")
        
        cue = CueInfo(
            cue_id=self.current_cue_id,
            track_id=None,
            file_path=self.file_path or "",
            duration_seconds=self.duration_seconds,
            gain_db=self.gain_db,
            in_frame=self.in_frame,
            out_frame=self.out_frame,
            fade_in_ms=self.fade_in_ms,
            loop_enabled=self.loop_enabled,
        )
        
        self.update_cue_settings.emit(self.current_cue_id, cue)
        print(f"[SoundFileButton._update_cue_settings] Signal emitted")

    # ==========================================================================
    # ENGINE ADAPTER SIGNAL HANDLERS
    # ==========================================================================

    def _on_cue_started(self, cue_id: str, cue_info: object) -> None:
        """
        Handle CueStartedEvent from engine adapter.
        
        Only responds to cues this button owns.
        """
        start = time.perf_counter()
        # Only respond if this cue_id belongs to this button
        if cue_id not in self._active_cue_ids:
            return
        
        # This cue belongs to us; mark button as playing
        self.current_cue_id = cue_id
        self.is_playing = True
        self.fade_button.setEnabled(True)
        self.fade_button.show()
        # Start flash timer (will handle repaints on flash events)
        self._start_flash()
        
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > 1.0:
            print(f"[PERF] SoundFileButton._on_cue_started: {elapsed:.2f}ms cue_id={cue_id}")
    
    def _on_cue_finished(self, cue_id: str, cue_info: object, reason: str) -> None:
        """
        Handle CueFinishedEvent from engine adapter.
        
        Only responds to cues this button owns. Resets UI when all owned cues finish.
        """
        start = time.perf_counter()
        # Only respond if this cue_id belongs to this button
        if cue_id not in self._active_cue_ids:
            return
        
        # Remove this cue from our active set
        self._active_cue_ids.discard(cue_id)
        
        # If all our cues have finished, reset the button state
        if not self._active_cue_ids:
            # Update state immediately (fast)
            self.is_playing = False
            self.elapsed_seconds = 0.0
            self.remaining_seconds = self.duration_seconds
            self.light_level = 0.0
            self.current_cue_id = None
        
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > 1.0:
            print(f"[PERF] SoundFileButton._on_cue_finished: {elapsed:.2f}ms cue_id={cue_id} reason={reason}")
    
    def _finish_cleanup(self) -> None:
        """
        Deferred cleanup after cue finishes. Called via QTimer.singleShot(0) to batch
        with other finish events, so multiple button repaints happen together instead of sequentially.
        Batches all UI updates into single stylesheet set + single repaint.
        """
        if not self._active_cue_ids:
            start = time.perf_counter()
            # Stop flash animation without triggering extra stylesheet work mid-cleanup
            if self.flash_anim is not None:
                self.flash_anim.stop()
                self.flash_anim.deleteLater()
                self.flash_anim = None
            
            # Clear tooltip
            self.setToolTip("")
            
            # Restore original color in ONE stylesheet update (not two)
            if self.bg_color or self.text_color:
                self._apply_stylesheet()
            else:
                self.setStyleSheet("")
            
            # Single repaint batches everything together
            self.update()
            
            # Defer fade button hide slightly (prevents 10 buttons from layout hopping at once)
            QTimer.singleShot(5, self._hide_fade_button)
            
            elapsed = (time.perf_counter() - start) * 1000
            if elapsed > 1.5:
                print(f"[PERF] SoundFileButton._finish_cleanup: {elapsed:.2f}ms cue={self.current_cue_id}")
    
    def _hide_fade_button(self) -> None:
        """Deferred fade button hide to batch layout updates."""
        self.fade_button.setEnabled(False)
        self.fade_button.hide()
    
    def _on_cue_time(self, cue_id: str, elapsed: float, remaining: float, total: Optional[float]) -> None:
        """
        Handle CueTimeEvent from engine adapter.
        Updates time display for owned cues.
        """
        start = time.perf_counter()
        if cue_id not in self._active_cue_ids:
            return
        
        self.elapsed_seconds = elapsed
        self.remaining_seconds = self.duration_seconds - elapsed
        self._update_time_display()
        
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms > 2.0:
            print(f"[PERF] SoundFileButton._on_cue_time: {elapsed_ms:.2f}ms cue_id={cue_id}")
    
    def _on_cue_levels(self, cue_id: str, rms, peak) -> None:
        """
        Handle CueLevelsEvent from engine adapter.
        
        Supports both formats:
        - Legacy: rms and peak as single floats (mixed levels)
        - Per-channel: rms and peak as lists (one value per audio channel)
        
        Updates level display for owned cues.
        Also updates the audio level meters if they are visible.
        """
        start = time.perf_counter()
        if cue_id not in self._active_cue_ids:
            return
        
        # Determine if we have per-channel or mixed levels
        is_per_channel = isinstance(rms, (list, tuple)) and isinstance(peak, (list, tuple))
        
        if is_per_channel:
            # Per-channel format: use channel 0 for light indicator, apply per-channel to meters
            rms_mono = rms[0] if len(rms) > 0 else 0.0
            peak_mono = peak[0] if len(peak) > 0 else 0.0
            rms_levels = rms
            peak_levels = peak
        else:
            # Legacy mixed format: single values for both
            rms_mono = rms
            peak_mono = peak
            rms_levels = [rms]
            peak_levels = [peak]
        
        self.rms_level = rms_mono
        self.peak_level = peak_mono
        min_level = 0.3
        max_level = 0.5
        level = rms_mono
        level = 1.0 - abs((level-min_level)/(max_level-min_level))
        self.light_level = int(255 * level)
        if self.light_level > 255:
            self.light_level = 255
        
        # Update audio level meters if they should be receiving updates (when slider is active/visible)
        if self.meters_should_update:
            # Convert linear RMS to dB for meter display
            # RMS values are typically in range [0, 1] (linear), but meter expects dB range [-64, 0]
            # Use standard formula: dB = 20 * log10(rms)
            # Clamp small values to avoid log(0) error
            
            # Update left meter (channel 0)
            if len(rms_levels) > 0 and len(peak_levels) > 0:
                rms_safe = max(rms_levels[0], 1e-10)  # Prevent log10(0) error
                peak_safe = max(peak_levels[0], 1e-10)
                
                try:
                    rms_db = 20 * np.log10(rms_safe)
                    peak_db = 20 * np.log10(peak_safe)
                    
                    # Clamp to meter range
                    rms_db = max(-64.0, min(0.0, rms_db))
                    peak_db = max(-64.0, min(0.0, peak_db))
                except (ValueError, TypeError):
                    rms_db = -64.0
                    peak_db = -64.0
                
                self.level_meter_left.setValue(rms_db, peak_db)
                self.level_meter_left.update()
            
            # Update right meter (channel 1, or same as left if mono)
            if len(rms_levels) > 1 and len(peak_levels) > 1:
                rms_safe = max(rms_levels[1], 1e-10)
                peak_safe = max(peak_levels[1], 1e-10)
            else:
                # Stereo split: use channel 0 for both if only one channel
                rms_safe = max(rms_levels[0] if len(rms_levels) > 0 else 0.0, 1e-10)
                peak_safe = max(peak_levels[0] if len(peak_levels) > 0 else 0.0, 1e-10)
            
            try:
                rms_db = 20 * np.log10(rms_safe)
                peak_db = 20 * np.log10(peak_safe)
                
                # Clamp to meter range
                rms_db = max(-64.0, min(0.0, rms_db))
                peak_db = max(-64.0, min(0.0, peak_db))
            except (ValueError, TypeError):
                rms_db = -64.0
                peak_db = -64.0
            
            self.level_meter_right.setValue(rms_db, peak_db)
            self.level_meter_right.update()
        
        # Trigger repaint to update the playing indicator gradient
        self.update()
        
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > 2.0:
            print(f"[PERF] SoundFileButton._on_cue_levels: {elapsed:.2f}ms cue_id={cue_id}")

    # ==========================================================================
    # DRAG AND DROP HANDLING
    # ==========================================================================
    
    def dragEnterEvent(self, event) -> None:
        """Accept drag events from files or button drag-to-move."""
        mime_data = event.mimeData()
        
        # Check for button drag (move settings from another button to this one)
        if (SoundFileButton._dragging_button is not None and 
            SoundFileButton._dragging_button is not self):
            event.acceptProposedAction()
        # Check for file drag (load audio file from file manager)
        elif mime_data.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dragMoveEvent(self, event) -> None:
        """Accept drag move events over the widget."""
        mime_data = event.mimeData()
        if mime_data.hasUrls() or SoundFileButton._dragging_button is not None:
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dropEvent(self, event) -> None:
        """
        Handle dropped data - either button drag or file drop.
        
        If button drag:
        - Copy all settings from source button to this button
        
        If file drop:
        - First file goes to this button
        - Remaining files distribute to buttons to the right, then down to next row
        - Warning dialog shown if overwriting existing files
        """
        mime_data = event.mimeData()
        
        # Handle button drag (move settings from source button to this button)
        if (SoundFileButton._dragging_button is not None and 
            SoundFileButton._dragging_button is not self):
            # Check if target button has existing settings
            if self.file_path:
                # Show warning dialog asking if user wants to overwrite
                dialog = QMessageBox(self)
                dialog.setWindowTitle("Overwrite Settings")
                dialog.setText(f"This button already has settings loaded:\n\n{self.song_title or self.file_path}")
                dialog.setInformativeText("Do you want to replace them with the dragged button's settings?")
                dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
                dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
                
                if dialog.exec() != QMessageBox.StandardButton.Ok:
                    event.ignore()
                    return
            
            self._copy_from_button(SoundFileButton._dragging_button)
            event.acceptProposedAction()
            return
        
        # Handle file drop (load audio files)
        if not mime_data.hasUrls():
            event.ignore()
            return
        
        # Extract all file paths from dropped files
        urls = event.mimeData().urls()
        file_paths = [url.toLocalFile() for url in urls if url.toLocalFile()]
        
        if not file_paths:
            event.ignore()
            return
        
        # Load first file into this button
        self.file_path = file_paths[0]
        self._probe_file_async(file_paths[0])
        self._refresh_label()
        
        # If there are more files, distribute them to nearby buttons
        if len(file_paths) > 1:
            remaining_files = file_paths[1:]
            self._distribute_files_to_siblings(remaining_files)
        
        event.acceptProposedAction()
    
    def _distribute_files_to_siblings(self, file_paths: list[str]) -> None:
        """
        Distribute remaining files to buttons to the right and below.
        
        Walks right across the current row, then continues on the next row.
        Shows warning if a button already has a file.
        """
        # Find parent widget (button bank or grid container)
        parent = self.parent()
        if parent is None:
            return
        
        # Get all buttons from parent (look for SoundFileButton instances)
        all_buttons = [w for w in parent.findChildren(SoundFileButton) if isinstance(w, SoundFileButton)]
        if not all_buttons:
            return
        
        # Find current button's index
        try:
            current_index = all_buttons.index(self)
        except ValueError:
            return
        
        # Get geometry info to determine layout (assumes grid layout with fixed columns)
        # We'll use button positions to figure out the grid
        button_positions = [(btn, (btn.x(), btn.y())) for btn in all_buttons]
        button_positions.sort(key=lambda x: (x[1][1], x[1][0]))  # Sort by y, then x
        
        # Find buttons to the right and below the current button
        current_pos = (self.x(), self.y())
        buttons_to_fill = []
        
        for btn, (btn_x, btn_y) in button_positions:
            if btn is self:
                continue
            
            # Check if button is to the right on same row, or on next rows
            if btn_y == current_pos[1]:
                # Same row, only include if to the right
                if btn_x > current_pos[0]:
                    buttons_to_fill.append(btn)
            elif btn_y > current_pos[1]:
                # Below current row
                buttons_to_fill.append(btn)
        
        # Assign files to buttons
        buttons_to_warn = []
        
        for file_path, btn in zip(file_paths, buttons_to_fill):
            # Warn if button already has a file
            if btn.file_path:
                buttons_to_warn.append((btn, btn.file_path))
            
            # Update button with file
            btn.file_path = file_path
            btn._probe_file_async(file_path)
            btn._refresh_label()
        
        # Show warning dialog if any buttons were overwritten
        if buttons_to_warn:
            self._show_overwrite_warning(buttons_to_warn)
    
    def _show_overwrite_warning(self, overwritten: list[tuple]) -> None:
        """
        Show a warning dialog listing buttons that had their files overwritten.
        
        Args:
            overwritten: List of (button, old_file_path) tuples
        """
        from PySide6.QtWidgets import QMessageBox
        
        msg = "The following buttons already had files. They have been overwritten:\n\n"
        for btn, old_path in overwritten:
            filename = old_path.split("/")[-1].split("\\")[-1]
            msg += f"• {filename}\n"
        
        QMessageBox.warning(
            self,
            "Files Overwritten",
            msg,
            QMessageBox.StandardButton.Ok
        )

    # ==========================================================================
    # BUTTON-TO-BUTTON DRAGGING (MOVE SETTINGS)
    # ==========================================================================
    
    def mousePressEvent(self, event) -> None:
        """Track mouse press to detect drag start and swipe start."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self._swipe_start_pos = event.pos()
            self._swipe_start_time = time.time()
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event) -> None:
        """
        Detect swipes and button drags.
        
        Swipes are fast horizontal movements detected during the same mouse press.
        Button drags initiate after moving >10px and holding for 100ms+.
        """
        if event.buttons() & Qt.MouseButton.LeftButton:
            # If gestures are disabled, clear swipe tracking to allow dragging
            if not SoundFileButton.gesture_enabled:
                self._swipe_start_pos = None
            
            if self._swipe_start_pos and SoundFileButton.gesture_enabled:
                delta = event.pos() - self._swipe_start_pos
                distance = abs(delta.x())  # Horizontal distance
                vertical = abs(delta.y())   # Vertical distance
                
                # Detect swipe: horizontal motion > vertical motion, and < 50px total
                # This avoids interfering with button drag
                if distance > 15 and distance > vertical * 2 and distance < 50:
                    elapsed_time = (time.time() - self._swipe_start_time) if self._swipe_start_time else 0
                    # Swipe is fast horizontal movement (within 300ms)
                    if elapsed_time < 0.3:
                        if delta.x() < 0:  # Left swipe
                            self._show_gain_slider()
                            self._swipe_start_pos = None
                            return
                        elif delta.x() > 0:  # Right swipe
                            self._hide_gain_slider()
                            self._swipe_start_pos = None
                            return
            
            # Check for button drag (only if drag is enabled)
            if self._drag_start_pos and SoundFileButton.drag_enabled:
                distance = (event.pos() - self._drag_start_pos).manhattanLength()
                if distance > 10:  # Drag threshold of 10 pixels
                    self._start_button_drag()
                    self._drag_start_pos = None
        super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event) -> None:
        """Reset swipe tracking on mouse release."""
        self._swipe_start_pos = None
        self._swipe_start_time = None
        super().mouseReleaseEvent(event)
    
    def _start_button_drag(self) -> None:
        """Initiate a drag operation for this button."""
        drag = QDrag(self)
        mime_data = QMimeData()
        # Set custom MIME type to identify button drag
        mime_data.setText("button-drag")
        drag.setMimeData(mime_data)
        
        # Set button's visual feedback during drag
        drag.setPixmap(self.grab())
        
        # Mark this button as the source of the drag
        SoundFileButton._dragging_button = self
        drag.exec(Qt.DropAction.MoveAction)
        SoundFileButton._dragging_button = None
    
    def _copy_from_button(self, source: SoundFileButton) -> None:
        """
        Copy all settings from another button to this button.
        
        Args:
            source: The source button to copy from
        """
        # Copy file info
        self.file_path = source.file_path
        self.duration_seconds = source.duration_seconds
        self.sample_rate = source.sample_rate
        self.channels = source.channels
        self.song_title = source.song_title
        self.song_artist = source.song_artist
        
        # Copy clip parameters
        self.in_frame = source.in_frame
        self.out_frame = source.out_frame
        self.loop_enabled = source.loop_enabled
        self.gain_db = source.gain_db
        self.auto_fade_enabled = source.auto_fade_enabled
        
        # Copy fade durations
        self.fade_in_ms = source.fade_in_ms
        self.fade_out_ms = source.fade_out_ms
        
        # Copy colors
        self.bg_color = source.bg_color
        self.text_color = source.text_color
        
        # Update gain slider to reflect copied gain value (absolute position)
        if self.gain_slider:
            self.gain_slider.blockSignals(True)
            self.gain_slider.setValue(int(self.gain_db * 10))
            self.gain_slider.blockSignals(False)
        
        # Update gain value label to show current dB
        if self.gain_value_label:
            self.gain_value_label.setText(f"{self.gain_db:+.1f} dB")
        
        # Refresh display
        self._refresh_label()

    # ==========================================================================
    # RESIZE HANDLING FOR FADE BUTTON POSITIONING
    # ==========================================================================
    
    def resizeEvent(self, event: QResizeEvent) -> None:
        """
        Update FadeButton size and position when parent button is resized.
        Also recalculates font size and text wrapping based on new dimensions.
        Called automatically by Qt whenever the widget changes size.
        """
        super().resizeEvent(event)
        
        # Size the fade button to be 1/5 of the main button
        new_width = max(self.width() // 5, 20)  # Minimum 20px
        new_height = max(self.height() // 5, 20)
        self.fade_button.setFixedSize(new_width, new_height)
        
        # Position in upper right corner (accounting for button border)
        border_margin = 2
        x = self.width() - new_width - border_margin
        y = 20
        self.fade_button.move(x, y)
        
        # Update gain slider position if visible
        if self.gain_slider and self.gain_label:
            self._update_slider_position()
        
        # Recalculate font size and text wrapping based on new button size
        if self.file_path:
            display_name = self.song_title or self.file_path.split("/")[-1].split("\\")[-1]
            self.setText(self._auto_wrap_text(display_name))
        else:
            self.setText(self._auto_wrap_text("Choose Sound"))

    # ==========================================================================
    # GESTURE HANDLING FOR GAIN SLIDER (SWIPE LEFT/RIGHT)
    # ==========================================================================
    
    # Swipe detection is handled in mousePressEvent/mouseMoveEvent/mouseReleaseEvent
    # Swipes are detected as fast horizontal movements (< 300ms, > 15px horizontal)
    
    def _setup_gain_slider(self) -> None:
        """
        Create and configure the gain slider widget.
        Positioned on the right side, initially hidden.
        
        Slider range: -120dB (silence/mute) to +30dB (very loud)
        - Bottom (0): -120dB (nearly silent)
        - Middle (75): 0dB (unity/no change)
        - Top (150): +30dB (30x amplitude increase)
        """
        # Create slider (vertical) with tick marks at 0dB
        self.gain_slider = QSlider(Qt.Orientation.Vertical)
        self.gain_slider.setRange(-640, 200)  # -64dB to +20dB, stored as 0.1dB increments
        self.gain_slider.setValue(int(self.gain_db * 10))  # Store as 0.1dB increments
        self.gain_slider.setParent(self)
        # Add tick marks for reference
        self.gain_slider.setTickPosition(QSlider.TickPosition.TicksLeft)
        self.gain_slider.setTickInterval(100)  # Tick every 10dB
        self.gain_slider.setStyleSheet("""
            QSlider::groove:vertical {
                border: 1px solid #999;
                width: 8px;
                margin: 2px 0;
                background: #333;
            }
            QSlider::handle:vertical {
                background: #4CAF50;
                border: 1px solid #999;
                height: 18px;
                margin: -5px 0;
                border-radius: 3px;
            }
            QSlider::handle:vertical:hover {
                background: #66BB6A;
            }
            QSlider::sub-page:vertical {
                background: #2196F3;
            }
        """)
        self.gain_slider.sliderMoved.connect(self._on_gain_slider_changed)
        self.gain_slider.valueChanged.connect(self._on_gain_slider_changed)
        
        # Create "Reset Gain" button (replaces the "gain" label)
        self.gain_reset_button = QPushButton("Reset")
        self.gain_reset_button.setParent(self)
        self.gain_reset_button.setStyleSheet("""
            QPushButton {
                background-color: #FF6B6B;
                color: white;
                font-size: 8px;
                font-weight: bold;
                border: 1px solid #999;
                border-radius: 2px;
                padding: 1px;
            }
            QPushButton:hover {
                background-color: #FF8787;
            }
            QPushButton:pressed {
                background-color: #E63946;
            }
        """)
        self.gain_reset_button.clicked.connect(self._on_reset_gain)
        
        # Create label for current gain value display
        self.gain_value_label = QLabel(f"{self.gain_db:+.1f} dB")
        self.gain_value_label.setParent(self)
        self.gain_value_label.setStyleSheet("color: #4CAF50; font-size: 8px; font-weight: bold;")
        self.gain_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Create audio level meters for L/R channels (very narrow, 10px wide)

        # These will be displayed to the left of the gain slider
        # Height will be set in _update_slider_position based on available space
        self.level_meter_left = AudioLevelMeter(vmin=-64, vmax=0, height=100, width=10)
        self.level_meter_left.setParent(self)
       
        
        self.level_meter_right = AudioLevelMeter(vmin=-64, vmax=0, height=100, width=10)
        self.level_meter_right.setParent(self)
        
        self.level_meter_left.setValue(-64, -64)
        self.level_meter_right.setValue(-64, -64)
        
        # Hide initially
        self.gain_slider.hide()
        self.gain_reset_button.hide()
        self.gain_value_label.hide()
        self.level_meter_left.hide()
        self.level_meter_right.hide()
        self.gain_slider_visible = False
    
    def _update_slider_position(self) -> None:
        """
        Position the gain slider, reset button, value label, and level meters on the right side of the button.
        Called on resize or when showing the slider.
        
        Layout (from bottom to top):
        - gain_reset_button ("Reset" button) - bottom
        - gain_value_label (current dB value) - middle
        - gain_slider - top (takes remaining space)
        - level_meter_left and level_meter_right - left of slider (10px wide each, stacked vertically)
        """
        if not self.gain_slider or not self.gain_reset_button or not self.gain_value_label:
            return
        
        # Dimensions
        slider_width = 30
        meter_width = 10
        button_height = 18
        label_height = 14
        total_height = button_height + label_height + 2
        slider_height = self.height() - total_height - 4
        
        # Position bottom button ("Reset" button)
        button_x = self.width() - slider_width - 2
        button_y = self.height() - button_height - 2
        self.gain_reset_button.setGeometry(button_x, button_y, slider_width, button_height)
        
        # Position middle label (gain value like "+6.5 dB")
        value_label_y = button_y - label_height - 1
        self.gain_value_label.setGeometry(button_x, value_label_y, slider_width, label_height)

        # Position slider above button and label
        slider_x = self.width() - slider_width - 2
        slider_y = 2
        self.gain_slider.setGeometry(slider_x, slider_y, slider_width, slider_height)
        
        # Position level meters horizontally to the left of the slider
        # Meters are laid out side-by-side horizontally
        meter_height = self.height() - total_height - 2
        meter_y = 2
        
        if self.channels and self.channels >= 2:
            # Stereo: place meters side by side (left and right)
            meter_width_half = meter_width
            meter_left_x = slider_x - (meter_width_half * 2) - 4  # 2 meters + 2px gaps
            
            # Left channel meter (left side)
            self.level_meter_left.setFixedHeight(meter_height)
            self.level_meter_left.setGeometry(meter_left_x, meter_y, meter_width_half, meter_height)
            
            # Right channel meter (right side, next to left)
            self.level_meter_right.setFixedHeight(meter_height)
            self.level_meter_right.setGeometry(meter_left_x + meter_width_half + 2, meter_y, meter_width_half, meter_height)
        else:
            # Mono: single meter
            meter_left_x = slider_x - meter_width - 2
            self.level_meter_left.setFixedHeight(meter_height)
            self.level_meter_left.setGeometry(meter_left_x, meter_y, meter_width, meter_height)
            # Right meter hidden for mono
            self.level_meter_right.hide()
    

    def _animate_slider_in(self) -> None:
        """Animate the gain slider, button, label, and level meters sliding in from the right."""
        if not self.gain_slider:
            return
        
        # Stop any existing animation
        if self.slider_animation:
            self.slider_animation.stop()
        
        # Calculate positions without animation first
        self._update_slider_position()
        
        # Get the final position
        final_x = self.gain_slider.x()
        start_x = self.width()  # Start off-screen to the right
        
        # Calculate offset for all widgets
        offset = start_x - final_x
        
        # Move all widgets off-screen to start
        slider_geom = self.gain_slider.geometry()
        button_geom = self.gain_reset_button.geometry()
        label_geom = self.gain_value_label.geometry()
        meter_left_geom = self.level_meter_left.geometry()
        meter_right_geom = self.level_meter_right.geometry()
        
        self.gain_slider.setGeometry(slider_geom.x() + offset, slider_geom.y(), slider_geom.width(), slider_geom.height())
        self.gain_reset_button.setGeometry(button_geom.x() + offset, button_geom.y(), button_geom.width(), button_geom.height())
        self.gain_value_label.setGeometry(label_geom.x() + offset, label_geom.y(), label_geom.width(), label_geom.height())
        self.level_meter_left.setGeometry(meter_left_geom.x() + offset, meter_left_geom.y(), meter_left_geom.width(), meter_left_geom.height())
        if self.level_meter_right.isVisible() or self.channels and self.channels >= 2:
            self.level_meter_right.setGeometry(meter_right_geom.x() + offset, meter_right_geom.y(), meter_right_geom.width(), meter_right_geom.height())
        
        # Animate the x position from off-screen to final position
        self.slider_animation = QPropertyAnimation(self.gain_slider, b"geometry")
        self.slider_animation.setDuration(300)
        self.slider_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        self.slider_animation.setStartValue(self.gain_slider.geometry())
        self.slider_animation.setEndValue(slider_geom)
        
        # Connect to sync button, label, and meters
        self.slider_animation.valueChanged.connect(self._sync_slider_widgets_in)
        self.slider_animation.start()
    
    def _sync_slider_widgets_in(self) -> None:
        """Sync button, label, and meter positions during slide-in animation."""
        if not self.gain_slider or not self.gain_reset_button or not self.gain_value_label:
            return
        
        slider_x = self.gain_slider.x()
        slider_y = self.gain_slider.y()
        slider_width = self.gain_slider.width()
        slider_height = self.gain_slider.height()
        
        # Calculate button position relative to slider
        button_height = 18
        button_y = self.height() - button_height - 2
        self.gain_reset_button.setGeometry(slider_x, button_y, slider_width, button_height)
        
        # Calculate label position relative to slider
        label_height = 14
        label_y = button_y - label_height - 1
        self.gain_value_label.setGeometry(slider_x, label_y, slider_width, label_height)
        
        # Calculate meter positions relative to slider (horizontally to the left)
        meter_width = 10
        total_height = button_height + label_height + 2
        meter_height = self.height() - total_height - 2
        meter_y = 2
        
        if self.channels and self.channels >= 2:
            meter_left_x = slider_x - (meter_width * 2) - 4
            self.level_meter_left.setGeometry(meter_left_x, meter_y, meter_width, meter_height)
            self.level_meter_right.setGeometry(meter_left_x + meter_width + 2, meter_y, meter_width, meter_height)
        else:
            meter_x = slider_x - meter_width - 2
            self.level_meter_left.setGeometry(meter_x, meter_y, meter_width, meter_height)

    
    def _animate_slider_out(self) -> None:
        """Animate the gain slider, button, label, and level meters sliding out to the right."""
        if not self.gain_slider:
            return
        
        # Stop any existing animation
        if self.slider_animation:
            self.slider_animation.stop()
        
        # Get current positions
        slider_geom = self.gain_slider.geometry()
        button_geom = self.gain_reset_button.geometry()
        label_geom = self.gain_value_label.geometry()
        meter_left_geom = self.level_meter_left.geometry()
        meter_right_geom = self.level_meter_right.geometry()
        
        # Calculate end position (off-screen to right)
        offset = self.width()  # Move everything off-screen
        
        end_slider_geom = QRect(slider_geom.x() + offset, slider_geom.y(), slider_geom.width(), slider_geom.height())
        
        # Animate slider to off-screen
        self.slider_animation = QPropertyAnimation(self.gain_slider, b"geometry")
        self.slider_animation.setDuration(300)
        self.slider_animation.setEasingCurve(QEasingCurve.Type.InCubic)
        
        self.slider_animation.setStartValue(slider_geom)
        self.slider_animation.setEndValue(end_slider_geom)
        
        # Connect to sync button, label, and meters, then hide
        self.slider_animation.valueChanged.connect(self._sync_slider_widgets_out)
        self.slider_animation.finished.connect(self._hide_slider_widgets)
        self.slider_animation.start()
    
    def _sync_slider_widgets_out(self) -> None:
        """Sync button, label, and meter positions during slide-out animation."""
        if not self.gain_slider or not self.gain_reset_button or not self.gain_value_label:
            return
        
        slider_x = self.gain_slider.x()
        slider_y = self.gain_slider.y()
        slider_width = self.gain_slider.width()
        
        # Keep button and label aligned with slider during animation
        button_height = 18
        button_y = self.height() - button_height - 2
        self.gain_reset_button.setGeometry(slider_x, button_y, slider_width, button_height)
        
        label_height = 14
        label_y = button_y - label_height - 1
        self.gain_value_label.setGeometry(slider_x, label_y, slider_width, label_height)
        
        # Keep meters aligned with slider during animation (horizontally to the left)
        meter_width = 10
        total_height = button_height + label_height + 2
        meter_height = self.height() - total_height - 2
        meter_y = 2
        
        if self.channels and self.channels >= 2:
            meter_left_x = slider_x - (meter_width * 2) - 4
            self.level_meter_left.setGeometry(meter_left_x, meter_y, meter_width, meter_height)
            self.level_meter_right.setGeometry(meter_left_x + meter_width + 2, meter_y, meter_width, meter_height)
        else:
            meter_x = slider_x - meter_width - 2
            self.level_meter_left.setGeometry(meter_x, meter_y, meter_width, meter_height)
    
    def _hide_slider_widgets(self) -> None:
        """Hide slider, button, label, and meter widgets after animation completes."""
        self.gain_slider.hide()
        self.gain_reset_button.hide()
        self.gain_value_label.hide()
        self.level_meter_left.hide()
        self.level_meter_right.hide()
        self.meters_should_update = False  # Stop meter updates when slider is hidden
        self.update()
    
    def _show_gain_slider(self) -> None:
        """Show the gain slider with reset button, value label, and level meters, sliding in from the right."""
        if self.gain_slider_visible or not self.gain_slider:
            return
        
        self.gain_slider_visible = True
        self.meters_should_update = True  # Enable meter updates when slider is shown
        self._update_slider_position()
        self.gain_slider.show()
        self.gain_reset_button.show()
        self.gain_value_label.show()
        self.level_meter_left.show()
        self.level_meter_left.update()  # Force initial repaint
        if self.channels and self.channels >= 2:
            self.level_meter_right.show()
            self.level_meter_right.update()  # Force initial repaint
        
        # Animate sliding in from the right
        self._animate_slider_in()
        self.update()
    
    def _hide_gain_slider(self) -> None:
        """Hide the gain slider, reset button, and value label, sliding out to the right."""
        if not self.gain_slider_visible or not self.gain_slider:
            return
        
        self.gain_slider_visible = False
        
        # Animate sliding out to the right, then hide
        self._animate_slider_out()
    
    def _on_gain_slider_changed(self, value: int) -> None:
        """
        Handle gain slider value changes.
        Updates the gain_db and sends update to engine in real-time.
        
        The slider position always represents the ABSOLUTE gain level (not relative).
        
        Args:
            value: Slider value (in 0.1dB increments)
        """
        # Convert slider value (0.1dB increments) to actual dB
        new_gain_db = value / 10.0
        
        # Only update if value actually changed
        if abs(new_gain_db - self.gain_db) < 0.01:
            return
        
        self.gain_db = new_gain_db
        print(f"[SoundFileButton._on_gain_slider_changed] New gain: {self.gain_db} dB, is_playing: {self.is_playing}, current_cue_id: {self.current_cue_id}")
        
        # Update the gain value label to show current dB
        if self.gain_value_label:
            self.gain_value_label.setText(f"{self.gain_db:+.1f} dB")
        
        # Send update to engine using the same signal as loop state updates
        self._update_cue_settings()
        
        # Update display to show current gain value
        self.update()
    def _on_reset_gain(self) -> None:
        """Reset the gain to 0 dB when reset button is clicked."""
        self.gain_db = 0.0
        print(f"[SoundFileButton._on_reset_gain] Gain reset to 0 dB for cue_id={self.current_cue_id}")
        
        # Update slider position to represent 0 dB (position 0)
        if self.gain_slider:
            self.gain_slider.blockSignals(True)
            self.gain_slider.setValue(0)
            self.gain_slider.blockSignals(False)
        
        # Update the value label
        if self.gain_value_label:
            self.gain_value_label.setText("+0.0 dB")
        
        # Send update to engine
        self._update_cue_settings()
        
        # Update display
        self.update()