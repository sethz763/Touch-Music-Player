from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QFileDialog, QMenu
from PySide6.QtCore import Signal, QTimer, QCoreApplication, QObject
from typing import Optional

from engine.commands import PlayCueCommand


class ButtonEmitter(QObject):
    """Emitter for button signals."""
    request_play = Signal(str)  # file_path
    request_stop = Signal()
    request_fade = Signal(float)  # duration


class SoundFileButton(QPushButton):
    play_cue_command = Signal(PlayCueCommand)

    def __init__(self, label: str = "Empty", *, file_path: str | None = None) -> None:
        super().__init__(label)
        
        # Create emitter for signals
        self.emitter = ButtonEmitter()
        
        self.file_path: str | None = file_path
        # Probe metadata
        self.duration_seconds: Optional[float] = None
        self.sample_rate: Optional[int] = None
        self.channels: Optional[int] = None

        # Intentionally NOT feature complete (Step D freeze)
        self.current_cue_id: str | None = None
        self.is_active: bool = False

        # LOCKED context-menu policy fix
        self.setContextMenuPolicy(self.contextMenuPolicy().CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.clicked.connect(self._on_click)
        self._refresh_label()

    def _refresh_label(self) -> None:
        if self.file_path:
            name = self.file_path.split("/")[-1].split("\\")[-1]
            if self.duration_seconds:
                self.setText(f"{name} ({self._format_duration(self.duration_seconds)})")
            else:
                self.setText(name)
        else:
            self.setText("Choose Sound")

    def _format_duration(self, seconds: float) -> str:
        try:
            s = int(round(seconds))
            m, s = divmod(s, 60)
            h, m = divmod(m, 60)
            if h > 0:
                return f"{h}:{m:02d}:{s:02d}"
            return f"{m}:{s:02d}"
        except Exception:
            return "?"

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        choose = menu.addAction("Choose file…")
        act = menu.exec(self.mapToGlobal(pos))
        if act == choose:
            # Defer file dialog to next event loop iteration to avoid blocking audio pump
            QTimer.singleShot(0, self._choose_file)

    def _choose_file(self) -> None:
        # Note: QFileDialog.getOpenFileName() blocks the main thread while open.
        # Even though audio runs on a separate process, the Qt event loop (which drives
        # the audio pump timer) is blocked, causing brief audio hiccups.
        # This is a Qt limitation with blocking dialogs.
        fp, _ = QFileDialog.getOpenFileName(self, "Choose audio file", "", "Audio Files (*.wav *.mp3 *.flac *.aac *.m4a);;All Files (*)")
        if fp:
            self.file_path = fp
            # Probe file for quick duration/metadata
            try:
                self.duration_seconds, self.sample_rate, self.channels = self._probe_file(fp)
            except Exception:
                self.duration_seconds = None
                self.sample_rate = None
                self.channels = None
            self._refresh_label()

    def _on_click(self) -> None:
        if not self.file_path:
            self._choose_file()
            return
        # Emit PlayCueCommand with known total duration when available
        try:
            self.play_cue_command.emit(PlayCueCommand(file_path=self.file_path, total_seconds=self.duration_seconds))
        except Exception:
            self.play_cue_command.emit(PlayCueCommand(file_path=self.file_path))

    def _probe_file(self, path: str) -> tuple[Optional[float], Optional[int], Optional[int]]:
        """Best-effort probe: try av, fall back to wave for WAV files.

        Returns (duration_seconds, sample_rate, channels)
        """
        # Try av (fast metadata read)
        try:
            import av
            container = av.open(path)
            stream = next((s for s in container.streams if s.type == "audio"), None)
            total_seconds = None
            sr = None
            ch = None
            if stream is not None:
                try:
                    if getattr(stream, "duration", None) is not None and getattr(stream, "time_base", None) is not None:
                        total_seconds = float(stream.duration * stream.time_base)
                except Exception:
                    total_seconds = None
                try:
                    sr = int(getattr(stream, "rate", None)) if getattr(stream, "rate", None) is not None else None
                except Exception:
                    sr = None
                try:
                    ch = int(getattr(stream, "channels", None)) if getattr(stream, "channels", None) is not None else None
                except Exception:
                    ch = None
            try:
                container.close()
            except Exception:
                pass
            return (total_seconds, sr, ch)
        except Exception:
            pass

        # Fallback for WAV via wave
        try:
            import wave
            with wave.open(path, "rb") as w:
                frames = w.getnframes()
                sr = w.getframerate()
                ch = w.getnchannels()
                return (frames / float(sr) if sr else None, sr, ch)
        except Exception:
            return (None, None, None)

    # Slots for engine adapter signals
    def on_cue_started(self, cue_id: str) -> None:
        """Called when a cue starts playing."""
        if cue_id == self.current_cue_id:
            self.is_active = True

    def on_cue_finished(self, cue_id: str) -> None:
        """Called when a cue finishes playing."""
        if cue_id == self.current_cue_id:
            self.is_active = False

    def on_cue_time(self, cue_id: str, time_seconds: float) -> None:
        """Called when cue playback time updates."""
        if cue_id == self.current_cue_id:
            # Could update button UI with current time
            pass

    def on_cue_levels(self, cue_id: str, left: float, right: float) -> None:
        """Called when cue levels update."""
        if cue_id == self.current_cue_id:
            # Could update button UI with level visualization
            pass
