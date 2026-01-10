from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import time
from dataclasses import dataclass
import traceback
from typing import Any, Optional

from ui.widgets.AudioLevelMeter import AudioLevelMeter

import numpy as np

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, qRgb
from PySide6.QtWidgets import (
	QDial,
	QGridLayout,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QPushButton,
	QSizePolicy,
	QScrollArea,
	QSlider,
	QVBoxLayout,
	QWidget,
	QGestureEvent,
)

from engine.editor_audio_service import (
	Jog,
	JogStop,
	LoadFile,
	SetOutputDevice,
	Playhead,
	Levels,
	Loaded,
	Seek,
	SetGain,
	SetInOut,
	SetLoop,
	Shutdown,
	Status,
	TransportPause,
	TransportPlay,
	TransportStop,
	TransportFastForward,
	TransportRewind,
	start_editor_audio_backend,
)

from log.service_log import coerce_log_path


class ZoomableScrollArea(QScrollArea):
	"""QScrollArea subclass that captures pinch-to-zoom gestures.
	
	Emits scale_changed signal when user pinches; parent connects to this
	to drive scale updates.
	"""
	
	scale_changed = Signal(int)  # Emits new scale value
	
	def __init__(self, parent: Optional[QWidget] = None) -> None:
		super().__init__(parent)
		self.grabGesture(QtCore.Qt.GestureType.PinchGesture)
		self._current_scale = 50
		# Disable mouse wheel scrolling to prevent accidental viewport movement
		self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
	
	def set_scale(self, scale: int) -> None:
		self._current_scale = int(max(1, scale))
	
	def event(self, evt: QtCore.QEvent) -> bool:
		"""Override event to intercept gesture events."""
		if evt.type() == QtCore.QEvent.Type.Gesture:
			import os
			if os.environ.get('STEPD_PINCH_DEBUG') == '1':
				print(f"[ZoomableScrollArea.event] Got Gesture event", flush=True)
			if self._handle_pinch_gesture(evt):
				return True
		return super().event(evt)
	
	def gestureEvent(self, event: QGestureEvent) -> bool:
		"""Handle pinch-to-zoom gesture on scroll area.
		
		Pinch out (apart) = increase scale (zoom out, see more time).
		Pinch in (together) = decrease scale (zoom in, see less time).
		Scale range: 1 (individual samples) to ~10000 (long songs).
		"""
		return self._handle_pinch_gesture(event)
	
	def _handle_pinch_gesture(self, event: QGestureEvent) -> bool:
		"""Internal method to handle pinch gestures."""
		try:
			if not event:
				return False
			
			pinch = event.gesture(QtCore.Qt.GestureType.PinchGesture)
			if not pinch:
				return False
			
			try:
				scale_factor = float(pinch.scaleFactor())
			except (TypeError, AttributeError, ValueError):
				return False
			
			if scale_factor <= 0:
				return False
			
			# Apply scale factor to current scale (inverted for intuitive gesture)
			# Pinch expand (factor > 1) should zoom IN (decrease scale)
			# Pinch contract (factor < 1) should zoom OUT (increase scale)
			import math
			current_scale = int(self._current_scale)
			new_scale_float = current_scale / scale_factor  # Invert the factor
			
			# Use ceil() when zooming out (pinch contract, factor < 1)
			# Use floor() when zooming in (pinch expand, factor > 1)
			if scale_factor < 1.0:  # Pinch contract = zoom out
				new_scale = math.ceil(new_scale_float)
			else:  # Pinch expand = zoom in
				new_scale = int(new_scale_float)  # floor() via int()
			
			# Clamp to valid range
			new_scale = max(1, min(10000, new_scale))
			
			# Debug: log gesture info
			if __import__('os').environ.get('STEPD_PINCH_DEBUG') == '1':
				print(f"[ZoomableScrollArea] Pinch: factor={scale_factor:.3f}, {current_scale}->{new_scale}", flush=True)
			
			# Only update if scale changed meaningfully
			if new_scale != current_scale:
				self._current_scale = new_scale
				self.scale_changed.emit(new_scale)
			
			event.accept()
			return True
		
		except Exception as e:
			if __import__('os').environ.get('STEPD_PINCH_DEBUG') == '1':
				print(f"[ZoomableScrollArea] Pinch error: {e}", flush=True)
			return False


def _setup_editor_logging(component: str) -> tuple[logging.Logger, str]:
	"""Create a rotating log file for the editor UI.

	Default log path is under `service_log/`.
	Override with `STEPD_EDITOR_LOG_PATH` (will still be coerced into `service_log/`).
	"""

	safe_component = "".join(ch for ch in str(component) if ch.isalnum() or ch in ("_", "-")) or "ui"
	log_path_env = os.environ.get("STEPD_EDITOR_LOG_PATH")
	log_path = str(
		coerce_log_path(
			env_value=log_path_env,
			default_filename=f"audio_editor_{safe_component}.log",
			allow_absolute_outside_service_dir=False,
		)
	)

	name = f"stepd.editor.{component}"
	logger = logging.getLogger(name)
	try:
		if any(isinstance(h, RotatingFileHandler) for h in getattr(logger, "handlers", []) or []):
			return logger, log_path
	except Exception:
		pass

	level = logging.DEBUG if os.environ.get("STEPD_EDITOR_DEBUG", "0") == "1" else logging.INFO
	logger.setLevel(level)
	configured = False
	try:
		try:
			Path(str(log_path)).parent.mkdir(parents=True, exist_ok=True)
		except Exception:
			pass
		h = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
		h.setLevel(level)
		h.setFormatter(
			logging.Formatter(
				fmt="%(asctime)s %(levelname)s [%(processName)s:%(process)d] [%(threadName)s] %(name)s: %(message)s",
				datefmt="%Y-%m-%d %H:%M:%S",
			)
		)
		logger.addHandler(h)
		configured = True
	except Exception as e:
		# Last-ditch visibility: write setup failure to a simple text file.
		try:
			err_path = coerce_log_path(env_value=None, default_filename="audio_editor_logging_errors.txt")
			err_path.write_text(f"UI logger setup failed for {log_path}: {type(e).__name__}: {e}\n", encoding="utf-8")
		except Exception:
			pass

	logger.propagate = False
	return logger, log_path


def _append_editor_log_line(log_path: str, message: str) -> None:
	"""Best-effort file append.

	This is intentionally independent of the `logging` module to ensure we can
	always produce a log file even in weird launch contexts.
	"""
	try:
		ts = time.strftime("%Y-%m-%d %H:%M:%S")
		pid = os.getpid()
		p = Path(log_path)
		p.parent.mkdir(parents=True, exist_ok=True)
		with p.open("a", encoding="utf-8") as f:
			f.write(f"{ts} [pid={pid}] {message}\n")
	except Exception:
		pass


def _try_import_legacy_plotter():
	try:
		from legacy.plot_waveform_new import plot  # type: ignore

		return plot

	except Exception:
		return None


def _downsample_audio_for_display(pcm: np.ndarray, scale: int) -> np.ndarray:
	"""Return array shaped (channels, samples) suitable for legacy plotter.

	Preferred: legacy scale_audio (Cython). If unavailable, fallback to cheap stride.
	"""
	scale = int(max(1, scale))
	if pcm.ndim != 2:
		return np.zeros((2, 1), dtype=np.float32)

	# Try legacy Cython downsampler if present.
	try:
		from legacy.scale_audio import scale_audio  # type: ignore

		out = scale_audio(pcm.astype(np.float32, copy=False), int(scale), int(48000))
		# Unknown return convention; fall back if not an ndarray.
		if isinstance(out, np.ndarray):
			arr = out
			if arr.ndim == 2 and arr.shape[0] <= 2:
				return arr
	except Exception:
		pass

	# Fallback: stride-based downsample.
	try:
		ds = pcm[::scale, :].astype(np.float32, copy=False)
		return ds.T
	except Exception:
		return np.zeros((2, 1), dtype=np.float32)


class WaveformDisplay(QLabel):
	def __init__(self, parent: QWidget) -> None:
		super().__init__(parent)
		self.setStyleSheet(f"background-color: {QColor(0, 200, 250).name()};")
		# Match legacy editor default waveform height.
		self.setFixedHeight(150)
		# Ensure the widget participates in layout even before waveform data arrives.
		self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
		self.setMinimumWidth(500)
		self._status_text = "Loading waveform…"
		# Don't grab gestures here; parent ZoomableScrollArea will handle them
		self._parent_editor = parent  # Store reference to editor for scale changes

		self.scale = 50
		self.sample_rate = 48000
		self.channels = 2
		self.duration_frames = 0

		self.audio_level_array = np.zeros((2, 1), dtype=np.float32)
		self._plot_cls = _try_import_legacy_plotter()
		self._plotter = None

		self.scroll_pos = 0
		self.visible_width = 500
		self.position_frame = 0
		self.in_point_frame = 0
		self.out_point_frame = 0

	def set_audio(self, audio_level_array: np.ndarray, duration_frames: int, sample_rate: int, channels: int) -> None:
		self.audio_level_array = audio_level_array
		self.duration_frames = int(max(0, duration_frames))
		self.sample_rate = int(sample_rate)
		self.channels = int(max(1, channels))
		# Ensure the widget has a meaningful width so QScrollArea scrolling works,
		# but keep it resizable with the window.
		try:
			self.setMinimumWidth(int(max(1, self.audio_level_array.shape[1])))
		except Exception:
			self.setMinimumWidth(1)
		if self._plot_cls is not None:
			try:
				self._plotter = self._plot_cls(audio=self.audio_level_array)
				self._status_text = ""
			except Exception:
				self._plotter = None
				self._status_text = "Waveform render unavailable"
		self.update()

	def set_status(self, text: str) -> None:
		self._status_text = str(text or "")
		self.update()

	def set_scale(self, scale: int) -> None:
		self.scale = int(max(1, scale))

	def set_position_frame(self, frame: int) -> None:
		self.position_frame = int(max(0, frame))
		self.update()

	def set_visible(self, scroll_pos: int, viewport_width: int) -> None:
		self.scroll_pos = int(max(0, scroll_pos))
		self.visible_width = int(max(50, viewport_width))
		self.update()

	def gestureEvent(self, event: QGestureEvent) -> bool:
		"""Forward gesture events to parent scroll area for handling."""
		# Don't handle gestures here; let parent ZoomableScrollArea handle them
		return False

	def paintEvent(self, event: QtGui.QPaintEvent) -> None:
		painter = QtGui.QPainter(self)
		pen = QtGui.QPen()

		if self._plotter is None or self.duration_frames <= 0:
			painter.fillRect(self.rect(), QColor(0, 200, 250))
			painter.setPen(QColor(0, 0, 0))
			msg = self._status_text or "Loading waveform…"
			painter.drawText(10, 20, msg)
			painter.end()
			return

		try:
			zoom_scale = float(self.audio_level_array.shape[1]) / float(self.duration_frames)
		except Exception:
			zoom_scale = 1.0

		try:
			self._plotter.plot_waveform(
				painter=painter,
				pen=pen,
				scroll_pos=int(self.scroll_pos),
				height=int(self.height()),
				width=int(self.visible_width),
				position=int(self.position_frame),
				thickness=float(1.0),
				duration=int(self.duration_frames),
				scale=float(zoom_scale),
				in_point=int(self.in_point_frame),
				out_point=int(self.out_point_frame),
			)
		except Exception:
			painter.fillRect(self.rect(), QColor(0, 200, 250))
			painter.setPen(QColor(0, 0, 0))
			painter.drawText(10, 20, "Waveform render unavailable")
		finally:
			painter.end()


@dataclass
class _EditorModel:
	file_path: str
	track_id: str
	in_point_s: float
	out_point_s: Optional[float]
	gain_db: float
	loop_enabled: bool
	duration_s: float
	sample_rate: int
	channels: int
	metadata: dict[str, Any]


class AudioEditorWindow(QWidget):
	"""UI-only audio editor window.

	Owns no sounddevice streams; communicates with `engine.editor_audio_service`.
	"""

	cue_edits_committed = Signal(str, float, float, float, bool, float, object)
	# (track_id, in_s, out_s, gain_db, loop, duration_s, metadata_dict)

	def __init__(
		self,
		*,
		file_path: str,
		track_id: str,
		in_point_s: float = 0.0,
		out_point_s: Optional[float] = None,
		gain_db: float = 0.0,
		loop_enabled: bool = False,
		parent: Optional[QWidget] = None,
	) -> None:
		super().__init__(parent)

		self._logger, self._log_path = _setup_editor_logging("ui")
		self._logger.info("AudioEditorWindow open track_id=%s file_path=%s", track_id, file_path)
		_append_editor_log_line(self._log_path, f"AudioEditorWindow open track_id={track_id} file_path={file_path}")

		self._model = _EditorModel(
			file_path=file_path,
			track_id=track_id,
			in_point_s=float(max(0.0, in_point_s)),
			out_point_s=float(out_point_s) if out_point_s is not None else None,
			gain_db=float(gain_db),
			loop_enabled=bool(loop_enabled),
			duration_s=0.0,
			sample_rate=48000,
			channels=2,
			metadata={},
		)

		self.setWindowTitle(f"Audio Editor - {track_id}")
		# Use a normal top-level window so Windows shows the title bar and system buttons.
		# Note: setWindowFlags() replaces all flags; don't set only a hint flag.
		self.setWindowFlags(QtCore.Qt.WindowType.Window)
		# Some Qt/Windows setups won't paint QWidget stylesheet backgrounds unless
		# WA_StyledBackground/autoFill are enabled.
		try:
			self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
			self.setAutoFillBackground(True)
		except Exception:
			pass
		self.setStyleSheet("background-color: light gray;")
		self.setFixedSize(550, 435)

		# Backend process
		try:
			self._proc, self._cmd_conn, self._evt_conn = start_editor_audio_backend()
			if not self._proc or not self._cmd_conn or not self._evt_conn:
				raise RuntimeError("Backend startup returned invalid connections")
			if self._proc.exitcode is not None and self._proc.exitcode != 0:
				raise RuntimeError(f"Backend process exited with code {self._proc.exitcode}")
		except Exception as e:
			try:
				self._logger.error("Failed to start audio backend: %s", e)
				_append_editor_log_line(self._log_path, f"ERROR: Backend startup failed: {type(e).__name__}: {e}")
			except Exception:
				pass
			raise RuntimeError(f"Failed to start audio backend: {type(e).__name__}: {e}") from e
		self._closing = False

		# Diagnostics (no UI)
		self._last_backend_status_text: str = ""

		# Command sending
		# Prefer direct Pipe sends from the Qt thread: messages are small and this is
		# more reliable than a background sender thread on Windows+Qt.

		# Waveform PCM cache (UI process only)
		self._pcm_full: Optional[np.ndarray] = None
		self._waveform_thread: Optional[threading.Thread] = None

		# Waveform viewport tracking (for partial render when enough audio decoded)
		self._waveform_view_state_lock = threading.Lock()
		self._waveform_view_scroll_pos = 0
		self._waveform_view_viewport_width = 500
		self._waveform_view_scale = 50

		# Waveform slider interaction state
		self._slider_dragging = False
		self._slider_was_playing = False
		self._slider_last_seek_monotonic = 0.0

		self._build_ui()
		self._wire_ui()

		# Initialize viewport state after widgets exist.
		try:
			with self._waveform_view_state_lock:
				self._waveform_view_scale = int(getattr(self.waveform, "scale", 50))
				try:
					self._waveform_view_viewport_width = int(self.scroll_area.viewport().width())
				except Exception:
					self._waveform_view_viewport_width = 500
				try:
					self._waveform_view_scroll_pos = int(self.scroll_area.horizontalScrollBar().value())
				except Exception:
					self._waveform_view_scroll_pos = 0
		except Exception:
			pass

		# Start backend + load
		# Register with MainWindow (if present) so live output-device changes can be applied.
		try:
			mw = self._find_main_window()
			reg = getattr(mw, "_register_audio_editor_window", None)
			if callable(reg):
				reg(self)
		except Exception:
			pass

		self._send(LoadFile(self._model.file_path, self._initial_output_device()))
		self._send(SetGain(self._model.gain_db))
		self._send(SetLoop(self._model.loop_enabled))
		self._send(SetInOut(self._model.in_point_s, self._model.out_point_s))
		self._send(Seek(self._model.in_point_s))

		# Poll backend events
		self._evt_timer = QTimer(self)
		self._evt_timer.timeout.connect(self._drain_events)
		self._evt_timer.start(30)

		# Kick waveform build off-thread
		self._start_waveform_build()
		# After the event loop starts and the widget is shown, sync viewport-dependent
		# rendering state and ensure the window opens on-screen.
		try:
			QtCore.QTimer.singleShot(0, self._sync_waveform_viewport)
			QtCore.QTimer.singleShot(0, self._ensure_on_screen)
		except Exception:
			pass

	def _initial_output_device(self) -> Optional[int | str]:
		"""Best-effort: derive desired editor output device from MainWindow."""
		try:
			mw = self._find_main_window()
			dev = getattr(mw, "editor_output_device", None)
			if isinstance(dev, dict):
				idx = dev.get("index")
				if idx is None:
					return None
				try:
					return int(idx)
				except Exception:
					return idx
		except Exception:
			pass
		return None

	def _find_main_window(self) -> object:
		"""Best-effort: locate the MainWindow instance that owns settings/state."""
		candidates: list[object] = []
		try:
			p = self.parent()
			if p is not None:
				candidates.append(p)
		except Exception:
			pass
		try:
			w = self.window()
			if w is not None:
				candidates.append(w)
		except Exception:
			pass
		try:
			app = QtWidgets.QApplication.instance()
			if app is not None:
				aw = app.activeWindow()
				if aw is not None:
					candidates.append(aw)
		except Exception:
			pass

		for c in candidates:
			try:
				if c is None:
					continue
				# Heuristic: MainWindow has the registration methods and editor_output_device.
				if hasattr(c, "_register_audio_editor_window") or hasattr(c, "editor_output_device"):
					return c
			except Exception:
				continue

		# Fallback: return a dummy object so getattr() works.
		return object()

	def set_output_device(self, output_device: Optional[int | str]) -> None:
		"""Called by MainWindow when the Settings 'Editor Output' changes."""
		try:
			self._send(SetOutputDevice(output_device))
		except Exception:
			pass

	# ----------------------
	# UI construction
	# ----------------------

	def _build_ui(self) -> None:
		# Waveform display in a custom zoomable scroll area for viewport rendering into large label
		self.scroll_area = ZoomableScrollArea(self)
		self.waveform = WaveformDisplay(self)
		self.scroll_area.setWidget(self.waveform)
		self.scroll_area.setWidgetResizable(True)
		self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignLeft)
		self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
		self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # Hide scrollbar
		# Keep the waveform area from consuming all vertical space.
		self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

		self.waveform_slider = QSlider(Qt.Orientation.Horizontal)
		self.waveform_slider.setRange(0, 0)
		self.waveform_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

		
		self.meters_layout = QHBoxLayout()
		self.level_meter_ch1 = AudioLevelMeter(height=150,width=20)
		self.level_meter_ch2 = AudioLevelMeter(height=150,width=20)
		self.meters_layout.addWidget(self.level_meter_ch1)
		self.meters_layout.addWidget(self.level_meter_ch2)
  
		# Gain
		self.gain_slider = QSlider(Qt.Orientation.Vertical)
		self.gain_slider.setRange(-64, 30)
		self.gain_slider.setValue(int(round(self._model.gain_db)))	
		self.gain_line_edit = QLineEdit()
		self.gain_line_edit.setFixedWidth(40)
		self.gain_line_edit.setText(str(int(round(self._model.gain_db))))
		self.gain_label = QLabel("GAIN")
		self.gain_layout = QVBoxLayout()
		self.gain_layout.addWidget(self.gain_slider)
		self.gain_layout.addWidget(self.gain_line_edit)
		self.gain_layout.addWidget(self.gain_label)
		# Controls
		self.cue_in_button = QPushButton("GOT TO\nIN (U)")
		self.mark_in_button = QPushButton("MARK IN\n(I)")
		self.rewind_button = QPushButton("REW\n(J)")
		self.play_pause_button = QPushButton("PLAY/PAUSE\n(K)")
		self.stop_button = QPushButton("STOP")
		self.fast_forward_button = QPushButton("FFWD\n(L)")
		self.mark_out_button = QPushButton("MARK OUT\n(O)")
		self.cue_out_button = QPushButton("GOT TO\nOUT (P)")
		self.loop_button = QPushButton("LOOP")
		self.jog_dial = QDial()

		self.zoom_in_button = QPushButton("+")
		self.zoom_out_button = QPushButton("-")
		self.scale_labelA = QLabel("SCALE")
		self.scale_labelB = QLabel(str(self.waveform.scale))

		self.jog_dial.setFixedSize(200, 200)
		self.jog_dial.setWrapping(True)
		self.jog_dial.setNotchesVisible(True)
		self.jog_dial.setNotchTarget(10)
		self.jog_dial.setRange(0, 50)
		self._jog_position = 0

		# Time displays
		self.in_display = QLabel("00:00:00:00")
		self.out_display = QLabel("00:00:00:00")
		self.current_pos_display = QLabel("00:00:00:00")
		self.in_label = QLabel("IN POINT")
		self.out_label = QLabel("OUT POINT")
		self.current_pos_label = QLabel("PLAY POSITION")

		display_stylesheet = "background-color: 'black'; color: 'white'; border: 1px 'white';"
		font = QFont("Arial", 14)
		for w in (self.in_display, self.out_display, self.current_pos_display):
			w.setStyleSheet(display_stylesheet)
			w.setAlignment(Qt.AlignmentFlag.AlignHCenter)
			w.setFont(font)

		# Layout
		self.main_layout = QVBoxLayout()
		self.setLayout(self.main_layout)

		waveform_and_gain = QHBoxLayout()
		waveform_and_gain.addWidget(self.scroll_area)
		waveform_and_gain.addLayout(self.gain_layout)
		waveform_and_gain.addLayout(self.meters_layout)
		self.main_layout.addLayout(waveform_and_gain)
		self.main_layout.addWidget(self.waveform_slider)

		time_layout = QGridLayout()
		time_layout.addWidget(self.in_display, 0, 0)
		time_layout.addWidget(self.out_display, 0, 1)
		time_layout.addWidget(self.current_pos_display, 0, 2)
		time_layout.addWidget(self.in_label, 1, 0)
		time_layout.addWidget(self.out_label, 1, 1)
		time_layout.addWidget(self.current_pos_label, 1, 2)
		self.main_layout.addLayout(time_layout)

		master_controls = QGridLayout()
		controls_lower = QHBoxLayout()
		controls_upper = QHBoxLayout()
		controls_zoom = QHBoxLayout()

		
		controls_lower.addWidget(self.rewind_button)
		controls_lower.addWidget(self.play_pause_button)
		controls_lower.addWidget(self.stop_button)
		controls_lower.addWidget(self.fast_forward_button)
  
		controls_upper.addWidget(self.cue_in_button)
		controls_upper.addWidget(self.mark_in_button)
		controls_upper.addWidget(self.mark_out_button)
		controls_upper.addWidget(self.cue_out_button)

		controls_upper.addWidget(self.loop_button, alignment=Qt.AlignmentFlag.AlignLeft)
		# controls_upper.addLayout(controls_zoom)
		# controls_zoom.addWidget(self.zoom_in_button)
		# controls_zoom.addWidget(self.zoom_out_button)
		# controls_zoom.addWidget(self.scale_labelA, alignment=Qt.AlignmentFlag.AlignRight)
		# controls_zoom.addWidget(self.scale_labelB, alignment=Qt.AlignmentFlag.AlignLeft)

		master_controls.addLayout(controls_upper, 0, 0)
		master_controls.addLayout(controls_lower, 1, 0)
		master_controls.addWidget(self.jog_dial, 0, 1, 2, 1)
		self.main_layout.addLayout(master_controls)

		self._update_time_labels()

	def _wire_ui(self) -> None:
		self.gain_slider.valueChanged.connect(self._gain_slider_changed)
		self.gain_line_edit.textChanged.connect(self._gain_line_edit_changed)

		self.cue_in_button.clicked.connect(self._cue_in)
		self.cue_out_button.clicked.connect(self._cue_out)
		self.mark_in_button.clicked.connect(self._mark_in)
		self.mark_out_button.clicked.connect(self._mark_out)
		self.play_pause_button.clicked.connect(self._play_pause)
		self.stop_button.clicked.connect(self._stop)
		self.loop_button.clicked.connect(self._toggle_loop)
		self.zoom_in_button.clicked.connect(self._zoom_in)
		self.zoom_out_button.clicked.connect(self._zoom_out)

		self.waveform_slider.sliderPressed.connect(self._waveform_slider_pressed)
		self.waveform_slider.sliderReleased.connect(self._waveform_slider_released)
		self.waveform_slider.valueChanged.connect(self._waveform_slider_changed)
		self.scroll_area.horizontalScrollBar().valueChanged.connect(self._scroll_changed)
		self.scroll_area.scale_changed.connect(self._on_scroll_area_scale_changed)
		self.jog_dial.valueChanged.connect(self._jog_dial_changed)
		self.jog_dial.sliderReleased.connect(self._jog_dial_released)

	# ----------------------
	# Backend comms
	# ----------------------

	def _send(self, msg: object) -> None:
		try:
			self._cmd_conn.send(msg)
			try:
				if str(QtCore.qEnvironmentVariable("STEPD_EDITOR_DEBUG", "0")) == "1":
					_append_editor_log_line(self._log_path, f"UI sent cmd: {type(msg).__name__}")
			except Exception:
				pass
		except Exception as e:
			try:
				_append_editor_log_line(
					self._log_path,
					f"UI cmd send failed: {type(msg).__name__}: {type(e).__name__}: {e}",
				)
			except Exception:
				pass
			try:
				if str(QtCore.qEnvironmentVariable("STEPD_EDITOR_DEBUG", "0")) == "1":
					print(f"[AudioEditorWindow] cmd send failed: {type(e).__name__}: {e} ({type(msg).__name__})")
			except Exception:
				pass

	def _drain_events(self) -> None:
		if self._closing:
			return
		while True:
			try:
				if not self._evt_conn.poll(0):
					break
				evt = self._evt_conn.recv()
			except Exception:
				break

			if isinstance(evt, Loaded):
				self._model.duration_s = float(evt.duration_s)
				self._model.sample_rate = int(evt.sample_rate)
				self._model.channels = int(evt.channels)
				self._model.metadata = dict(evt.metadata or {})
				if self._model.out_point_s is None or self._model.out_point_s <= 0:
					self._model.out_point_s = self._model.duration_s
					self._send(SetInOut(self._model.in_point_s, self._model.out_point_s))
				self._update_time_labels()

				# Auto-set initial waveform scale based on file length (zoomed out for long files)
				try:
					dur = float(self._model.duration_s)
					if dur < 10:
						scale = 100
					elif dur < 30:
						scale = 300
					elif dur < 60:
						scale = 800
					elif dur < 180:  # 3 minutes
						scale = 1500
					elif dur < 300:  # 5 minutes
						scale = 2000
					elif dur < 600:  # 10 minutes
						scale = 3000
					else:
						scale = 4000
					self.waveform.set_scale(scale)
					try:
						with self._waveform_view_state_lock:
							self._waveform_view_scale = int(scale)
					except Exception:
						pass
					self.scale_labelB.setText(str(scale))
					self._rebuild_waveform_for_scale()
				except Exception:
					pass

				# Force a waveform update after decoding starts (ensures visible on launch)
				try:
					self.waveform.update()
				except Exception:
					pass

			elif isinstance(evt, Playhead):
				self._set_playhead(float(evt.time_s))
    
			if isinstance(evt, Levels):		
				ch1_lvl, ch2_lvl = float(evt.rms_l), float(evt.rms_r)
    
				#convert to dbfs
				eps=1e-12
				ch1_lvl = 20 * np.log10(ch1_lvl + eps)
				ch2_lvl = 20 * np.log10(ch2_lvl + eps)
				self.level_meter_ch1.setValue(ch1_lvl, ch1_lvl)
				self.level_meter_ch2.setValue(ch2_lvl, ch1_lvl)	
	
	# ----------------------
	# Waveform building (background)
	# ----------------------

	def _start_waveform_build(self) -> None:
		if self._waveform_thread and self._waveform_thread.is_alive():
			return

		self.waveform.set_status("Decoding waveform…")
		try:
			self._logger.info("Waveform build start")
		except Exception:
			pass
		_append_editor_log_line(self._log_path, "Waveform build start")

		def worker():
			try:
				import av
				import time

				container = av.open(self._model.file_path)
				stream = next((s for s in container.streams if s.type == "audio"), None)
				if stream is None:
					try:
						self._logger.warning("Waveform build: no audio stream")
					except Exception:
						pass
					QtCore.QTimer.singleShot(0, lambda: self.waveform.set_status("No audio stream found"))
					return

				target_sr = 48000
				layout = "stereo"
				resampler = av.AudioResampler(format="fltp", layout=layout, rate=target_sr)

				est_duration_s: Optional[float] = None
				try:
					# Prefer container.duration (microseconds) when available.
					if getattr(container, "duration", None):
						est_duration_s = float(container.duration) / 1_000_000.0
				except Exception:
					est_duration_s = None
				if not est_duration_s:
					try:
						if stream.duration and stream.time_base:
							est_duration_s = float(stream.duration * stream.time_base)
					except Exception:
						est_duration_s = None

				samples_decoded = 0
				last_progress_t = time.monotonic()
				last_yield_t = time.monotonic()
				# Render a quick partial waveform early so the UI doesn't look stuck,
				# then refresh again once enough decoded audio exists to cover the
				# currently visible waveform viewport.
				quick_render_done = False
				viewport_fill_render_done = False
				quick_render_target_samples = int(target_sr * 2)
				viewport_fill_target_samples: Optional[int] = None
				last_partial_render_t = time.monotonic()
				last_viewport_target_update_t = time.monotonic()

				chunks: list[np.ndarray] = []
				for packet in container.demux(stream):
					for frame in packet.decode():
						now_yield = time.monotonic()
						if now_yield - last_yield_t >= 0.02:
							# Give other threads (incl. Qt main thread) a chance to run.
							time.sleep(0)
							last_yield_t = now_yield
						out_frames = resampler.resample(frame)
						if not out_frames:
							continue
						for out in out_frames:
							arr = out.to_ndarray()
							if arr is None or arr.size == 0:
								continue
							if arr.ndim == 1:
								arr = arr.reshape(1, -1)
							# to stereo
							if arr.shape[0] == 1:
								arr = np.vstack([arr, arr])
							elif arr.shape[0] > 2:
								arr = arr[:2, :]
							chunks.append(arr.T.astype(np.float32, copy=False))
							samples_decoded += int(arr.shape[1])

							now = time.monotonic()
							if now - last_progress_t >= 0.5:
								last_progress_t = now
								try:
									sec = float(samples_decoded) / float(target_sr)
									if est_duration_s and est_duration_s > 0:
										pct = int(min(100.0, 100.0 * (sec / est_duration_s)))
										msg = f"Decoding waveform… {pct}%"
									else:
										msg = f"Decoding waveform… {sec:.1f}s"
								except Exception:
									msg = "Decoding waveform…"
								QtCore.QTimer.singleShot(0, lambda m=msg: self.waveform.set_status(m))

							# Update the viewport-fill target occasionally (UI thread updates state).
							if now - last_viewport_target_update_t >= 0.25:
								last_viewport_target_update_t = now
								try:
									viewport_fill_target_samples = int(self._get_viewport_fill_target_samples(target_sr))
								except Exception:
									viewport_fill_target_samples = None

							def do_partial_render() -> None:
								try:
									partial_pcm = np.concatenate(chunks, axis=0)
									scale = int(getattr(self.waveform, "scale", 1))
									partial_arr = _downsample_audio_for_display(partial_pcm, scale)
									# Use the best known duration for scaling (backend is authoritative).
									try:
										model_frames = int(max(0.0, float(self._model.duration_s)) * float(target_sr))
									except Exception:
										model_frames = 0
									duration_frames = int(max(int(partial_pcm.shape[0]), model_frames))

									def apply_partial():
										self.waveform.set_audio(
											partial_arr,
											duration_frames=duration_frames,
											sample_rate=target_sr,
											channels=2,
										)
										self._update_waveform_markers()
										self._update_slider_range()
										self._sync_waveform_viewport()
										self.waveform.set_status("")

									QtCore.QTimer.singleShot(0, apply_partial)
								except Exception:
								# Ignore partial render failures; final render will still happen.
									pass

							# 1) Quick early render (unchanged intent: make UI responsive)
							if (not quick_render_done) and samples_decoded >= quick_render_target_samples:
								if now - last_partial_render_t >= 0.5:
									last_partial_render_t = now
									do_partial_render()
									quick_render_done = True

							# 2) Refresh once viewport can be fully drawn from decoded PCM.
							if (not viewport_fill_render_done) and viewport_fill_target_samples is not None:
								if samples_decoded >= int(viewport_fill_target_samples):
									if now - last_partial_render_t >= 0.5:
										last_partial_render_t = now
										# Debug log for viewport-fill refresh
										try:
											if os.environ.get("STEPD_EDITOR_DEBUG", "0") == "1":
												msg = (f"[waveform] viewport-fill refresh: samples_decoded={samples_decoded} "
													   f"target={viewport_fill_target_samples} scale={getattr(self.waveform, 'scale', None)} "
													   f"scroll={getattr(self, '_waveform_view_scroll_pos', None)} width={getattr(self, '_waveform_view_viewport_width', None)}")
												print(msg, flush=True)
											self._logger.info("Waveform viewport-fill refresh: samples_decoded=%d target=%s scale=%s scroll=%s width=%s", samples_decoded, viewport_fill_target_samples, getattr(self.waveform, 'scale', None), getattr(self, '_waveform_view_scroll_pos', None), getattr(self, '_waveform_view_viewport_width', None))
										except Exception:
											pass
										do_partial_render()
										viewport_fill_render_done = True

				try:
					container.close()
				except Exception:
					pass

				if not chunks:
					try:
						self._logger.warning("Waveform build: no decodable audio frames")
					except Exception:
						pass
					QtCore.QTimer.singleShot(0, lambda: self.waveform.set_status("No decodable audio frames"))
					return

				pcm = np.concatenate(chunks, axis=0)
				self._pcm_full = pcm

				# Downsample for initial display scale
				arr = _downsample_audio_for_display(pcm, self.waveform.scale)
				# Always prefer actual decoded frame count for correct seek mapping.
				duration_frames = int(pcm.shape[0])

				def apply():
					self.waveform.set_audio(arr, duration_frames=duration_frames, sample_rate=target_sr, channels=2)
					self._update_waveform_markers()
					self._update_slider_range()

				QtCore.QTimer.singleShot(0, apply)
				try:
					self._logger.info("Waveform build complete: frames=%s", int(pcm.shape[0]))
				except Exception:
					pass
				_append_editor_log_line(self._log_path, f"Waveform build complete frames={int(pcm.shape[0])}")
			except Exception as e:
				try:
					self._logger.exception("Waveform build failed")
				except Exception:
					pass
				_append_editor_log_line(self._log_path, f"Waveform build failed: {type(e).__name__}: {e}")
				try:
					log_name = Path(self._log_path).name
				except Exception:
					log_name = "audio_editor.log"
				details = f"Waveform decode failed: {type(e).__name__}: {e} (see {log_name})"
				try:
					if str(QtCore.qEnvironmentVariable("STEPD_EDITOR_DEBUG", "0")) == "1":
						print("[AudioEditorWindow]", details)
						print(traceback.format_exc())
				except Exception:
					pass
				QtCore.QTimer.singleShot(0, lambda: self.waveform.set_status(details))
				return

		self._waveform_thread = threading.Thread(target=worker, name="WaveformBuild", daemon=True)
		self._waveform_thread.start()

	def _get_viewport_fill_target_samples(self, sample_rate: int) -> int:
		"""Return decoded PCM frames needed to fill the currently visible viewport.

		We approximate 1px == 1 downsampled sample because the waveform widget's
		minimum width is set to `audio_level_array.shape[1]` and it is hosted in a
		QScrollArea.
		"""
		sr = int(max(1, sample_rate))
		try:
			with self._waveform_view_state_lock:
				scroll_pos = int(max(0, self._waveform_view_scroll_pos))
				viewport_w = int(max(50, self._waveform_view_viewport_width))
				scale = int(max(1, self._waveform_view_scale))
		except Exception:
			scroll_pos, viewport_w, scale = 0, 500, int(max(1, getattr(self.waveform, "scale", 50)))

		# Add a small margin to avoid repainting right at the boundary.
		margin_px = 32
		needed_downsampled = scroll_pos + viewport_w + margin_px
		needed_pcm_frames = int(max(0, needed_downsampled) * scale)
		# Always require at least a tiny amount of audio so the plotter has something.
		min_frames = int(0.25 * float(sr))
		return int(max(min_frames, needed_pcm_frames))

	def _rebuild_waveform_for_scale(self) -> None:
		pcm = self._pcm_full
		if pcm is None:
			return
		arr = _downsample_audio_for_display(pcm, self.waveform.scale)
		self.waveform.set_audio(arr, duration_frames=int(pcm.shape[0]), sample_rate=48000, channels=2)
		# Keep scroll area scale in sync
		try:
			self.scroll_area.set_scale(self.waveform.scale)
		except Exception:
			pass
		self._update_waveform_markers()
		self._update_slider_range()
		self._sync_waveform_viewport()

	# ----------------------
	# UI handlers
	# ----------------------

	def _millisec_to_strtime(self, ms: int) -> str:
		try:
			qtime = QtCore.QTime(0, 0, 0, 0).addMSecs(int(ms))
			time_string = qtime.toString("hh:mm:ss")
			msec_string = qtime.toString("zz")
			if len(msec_string) < 2:
				msec_string = msec_string + "0"
			return time_string + ":" + msec_string[0:2]
		except Exception:
			return "00:00:00:00"

	def _update_time_labels(self) -> None:
		self.in_display.setText(self._millisec_to_strtime(int(self._model.in_point_s * 1000.0)))
		out_s = self._model.out_point_s if self._model.out_point_s is not None else 0.0
		self.out_display.setText(self._millisec_to_strtime(int(out_s * 1000.0)))

	def _update_waveform_markers(self) -> None:
		sr = 48000
		self.waveform.in_point_frame = int(self._model.in_point_s * sr)
		out_s = self._model.out_point_s if self._model.out_point_s is not None else 0.0
		self.waveform.out_point_frame = int(out_s * sr)
		self.waveform.update()

	def _set_playhead(self, time_s: float) -> None:
		sr = 48000
		frame = int(max(0.0, float(time_s)) * sr)
		self.waveform.set_position_frame(frame)
		self.current_pos_display.setText(self._millisec_to_strtime(int(time_s * 1000.0)))
		# Keep the slider in sync with playhead (without feedback-seeking).
		# If the user is actively dragging, don't fight their input.
		if self._slider_dragging:
			return
		try:
			den = int(self.waveform.audio_level_array.shape[1])
			if den > 0 and int(self.waveform.duration_frames) > 0:
				zoom_scale = float(self.waveform.duration_frames) / float(den)
				v = int(float(frame) / zoom_scale)
				v = max(0, min(v, int(self.waveform_slider.maximum())))
				self.waveform_slider.blockSignals(True)
				self.waveform_slider.setValue(v)
				self.waveform_slider.blockSignals(False)
		except Exception:
			pass

	def _waveform_slider_pressed(self) -> None:
		# When the user starts dragging the slider, the slider becomes authoritative.
		self._slider_dragging = True
		self._slider_last_seek_monotonic = 0.0
		self._slider_was_playing = bool(getattr(self, "_is_playing", False))
		# Pause playback/scrub so playhead events don't steal control.
		if self._slider_was_playing:
			try:
				self._send(TransportPause())
			except Exception:
				pass
			self._is_playing = False

	def _waveform_slider_released(self) -> None:
		# Commit a final seek at release; resume playback if we paused it.
		self._slider_dragging = False
		try:
			self._waveform_slider_changed(int(self.waveform_slider.value()))
		except Exception:
			pass
		if self._slider_was_playing:
			try:
				self._send(TransportPlay())
			except Exception:
				pass
			self._is_playing = True
		self._slider_was_playing = False

	def _update_slider_range(self) -> None:
		try:
			maxv = int(self.waveform.audio_level_array.shape[1])
		except Exception:
			maxv = 0
		self.waveform_slider.blockSignals(True)
		self.waveform_slider.setRange(0, max(0, maxv - 1))
		self.waveform_slider.blockSignals(False)

	def _scroll_changed(self, value: int) -> None:
		try:
			viewport_w = self.scroll_area.viewport().width()
		except Exception:
			viewport_w = 500
		try:
			with self._waveform_view_state_lock:
				self._waveform_view_scroll_pos = int(value)
				self._waveform_view_viewport_width = int(viewport_w)
				self._waveform_view_scale = int(getattr(self.waveform, "scale", self._waveform_view_scale))
		except Exception:
			pass
		self.waveform.set_visible(int(value), int(viewport_w))
		self.waveform.update()

	def _sync_waveform_viewport(self) -> None:
		try:
			v = int(self.scroll_area.horizontalScrollBar().value())
		except Exception:
			v = 0
		self._scroll_changed(v)

	def _on_scroll_area_scale_changed(self, new_scale: int) -> None:
		"""Handle pinch-to-zoom from scroll area."""
		try:
			import os
			if os.environ.get('STEPD_PINCH_DEBUG') == '1':
				print(f"[_on_scroll_area_scale_changed] new_scale={new_scale}", flush=True)
			self.waveform.set_scale(new_scale)
			try:
				with self._waveform_view_state_lock:
					self._waveform_view_scale = int(new_scale)
			except Exception:
				pass
			self.scale_labelB.setText(str(new_scale))
			self._rebuild_waveform_for_scale()
		except Exception as e:
			import os
			if os.environ.get('STEPD_PINCH_DEBUG') == '1':
				print(f"[_on_scroll_area_scale_changed] error: {e}", flush=True)

	def _waveform_slider_changed(self, value: int) -> None:
		# Slider value is in downsampled samples; map to seconds and seek.
		try:
			den = float(self.waveform.audio_level_array.shape[1])
			if den <= 0.0:
				return
			zoom_scale = float(self.waveform.duration_frames) / den
			frame = int(float(value) * zoom_scale)
			time_s = float(frame) / 48000.0
		except Exception:
			time_s = 0.0

		# Update UI immediately so seeking works even when not playing.
		# (Backend only emits Playhead while playing.)
		try:
			self.waveform.set_position_frame(int(max(0.0, time_s) * 48000.0))
			self.current_pos_display.setText(self._millisec_to_strtime(int(time_s * 1000.0)))
		except Exception:
			pass

		# Avoid spamming IPC while dragging; throttle seek requests.
		now = time.monotonic()
		if self._slider_dragging and (now - float(self._slider_last_seek_monotonic) < 0.05):
			return
		self._slider_last_seek_monotonic = float(now)
		self._send(Seek(time_s))

	def _gain_slider_changed(self) -> None:
		g = float(self.gain_slider.value())
		self._model.gain_db = g
		self.gain_line_edit.blockSignals(True)
		self.gain_line_edit.setText(str(int(round(g))))
		self.gain_line_edit.blockSignals(False)
		self._send(SetGain(g))

	def _gain_line_edit_changed(self) -> None:
		try:
			value = int(self.gain_line_edit.text())
		except Exception:
			return
		if value < -64 or value > 30:
			return
		self.gain_slider.blockSignals(True)
		self.gain_slider.setValue(int(value))
		self.gain_slider.blockSignals(False)
		self._model.gain_db = float(value)
		self._send(SetGain(float(value)))

	def _cue_in(self) -> None:
		self._send(Seek(self._model.in_point_s))

	def _cue_out(self) -> None:
		out_s = self._model.out_point_s if self._model.out_point_s is not None else 0.0
		self._send(Seek(out_s))

	def _mark_in(self) -> None:
		# Use current playhead display as source of truth (best-effort).
		# Backend is authoritative and sends Playhead; we keep last in current_pos_display.
		try:
			# No parse; just use last known playhead position from waveform.
			self._model.in_point_s = float(self.waveform.position_frame) / 48000.0
		except Exception:
			self._model.in_point_s = 0.0
		self._update_time_labels()
		self._update_waveform_markers()
		self._send(SetInOut(self._model.in_point_s, self._model.out_point_s))

	def _mark_out(self) -> None:
		try:
			self._model.out_point_s = float(self.waveform.position_frame) / 48000.0
		except Exception:
			self._model.out_point_s = self._model.duration_s
		self._update_time_labels()
		self._update_waveform_markers()
		self._send(SetInOut(self._model.in_point_s, self._model.out_point_s))

	def _play_pause(self) -> None:
		# Toggle: if playing, pause; else play.
		# We don't track backend status; just toggle based on last click.
		if getattr(self, "_is_playing", False):
			self._send(TransportPause())
			self._is_playing = False
		else:
			self._send(TransportPlay())
			self._is_playing = True

	def _stop(self) -> None:
		self._send(TransportStop())
		self._is_playing = False

	def _toggle_loop(self) -> None:
		self._model.loop_enabled = not self._model.loop_enabled
		self._send(SetLoop(self._model.loop_enabled))
		if self._model.loop_enabled:
			color = QColor("grey")
		else:
			color = QColor(qRgb(230, 230, 230))
		self.loop_button.setStyleSheet(f"background-color: {color.name()}; color: black;")

	def _zoom_in(self) -> None:
		# Legacy naming: plus reduces scale (more zoom)
		s = self.waveform.scale
		if s > 5:
			s -= 5
		elif s > 1:
			s -= 1
		self.waveform.set_scale(s)
		self.scale_labelB.setText(str(s))
		self._rebuild_waveform_for_scale()

	def _zoom_out(self) -> None:
		s = self.waveform.scale
		if s < 5:
			s += 1
		else:
			s += 5
		self.waveform.set_scale(s)
		self.scale_labelB.setText(str(s))
		self._rebuild_waveform_for_scale()

	def _jog_dial_changed(self) -> None:
		v = int(self.jog_dial.value())
		if v == self._jog_position:
			return
		# Calculate delta units, handling wrapping
		delta_units = (v - self._jog_position) % 50
		if delta_units > 25:
			delta_units -= 50
		# Calculate delta degrees: dial has 50 units for 360 degrees
		degrees_per_unit = 360.0 / 50.0
		delta_degrees = delta_units * degrees_per_unit
		self._jog_position = v
		self._send(Jog(delta_degrees))

	def _jog_dial_released(self) -> None:
		self._send(JogStop())

	# ----------------------
	# Qt events
	# ----------------------

	def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
		if event.key() == Qt.Key.Key_J:
			self._send(TransportRewind())
		if event.key() == Qt.Key.Key_K:
			self._send(TransportPause())
		if event.key() == Qt.Key.Key_L:
			self._send(TransportFastForward())
		if event.key() == Qt.Key.Key_I:
			self._mark_in()
		if event.key() == Qt.Key.Key_O:
			self._mark_out()
		if event.key() == Qt.Key.Key_U:
			self._cue_in()
		if event.key() == Qt.Key.Key_P:
			self._cue_out()
		if event.key() == Qt.Key.Key_Equal:
			self._zoom_in()
		if event.key() == Qt.Key.Key_Minus:
			self._zoom_out()

	def closeEvent(self, event: QtGui.QCloseEvent) -> None:
		self._closing = True
		try:
			mw = self._find_main_window()
			unreg = getattr(mw, "_unregister_audio_editor_window", None)
			if callable(unreg):
				unreg(self)
		except Exception:
			pass
		try:
			self._evt_timer.stop()
		except Exception:
			pass

		try:
			self._send(Shutdown())
		except Exception:
			pass

		# Best-effort: give backend a moment to flush.
		try:
			if self._proc is not None and self._proc.is_alive():
				self._proc.join(timeout=0.5)
		except Exception:
			pass

		# Commit edits on close (minimal behavior; no extra buttons).
		in_s = float(max(0.0, self._model.in_point_s))
		out_s = float(self._model.out_point_s if self._model.out_point_s is not None else self._model.duration_s)
		out_s = max(in_s, out_s)
		self.cue_edits_committed.emit(
			str(self._model.track_id),
			float(in_s),
			float(out_s),
			float(self._model.gain_db),
			bool(self._model.loop_enabled),
			float(self._model.duration_s),
			dict(self._model.metadata or {}),
		)

		event.accept()

	def _ensure_on_screen(self) -> None:
		try:
			screen = self.screen() or QtWidgets.QApplication.primaryScreen()
			if screen is None:
				return
			avail = screen.availableGeometry()
			w = int(self.width())
			h = int(self.height())
			x = int(self.x())
			y = int(self.y())
			max_x = int(avail.right()) - w
			max_y = int(avail.bottom()) - h
			x = max(int(avail.left()), min(x, max_x))
			y = max(int(avail.top()), min(y, max_y))
			self.move(x, y)
		except Exception:
			pass

