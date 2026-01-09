from __future__ import annotations

import logging
import math
import mmap
import os
import queue
import tempfile
import threading
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import multiprocessing as mp
import multiprocessing.connection as mp_connection

import numpy as np


# NOTE: No Qt imports in this module (non-negotiable).


# =============================
# Commands (UI -> backend)
# =============================


@dataclass(frozen=True)
class LoadFile:
    path: str
    output_device: Optional[int | str] = None  # None => system default


@dataclass(frozen=True)
class SetOutputDevice:
    output_device: Optional[int | str] = None  # None => system default


@dataclass(frozen=True)
class SetInOut:
    in_s: float
    out_s: Optional[float]


@dataclass(frozen=True)
class SetGain:
    gain_db: float


@dataclass(frozen=True)
class SetLoop:
    loop: bool


@dataclass(frozen=True)
class TransportPlay:
    pass


@dataclass(frozen=True)
class TransportPause:
    pass


@dataclass(frozen=True)
class TransportStop:
    pass

@dataclass(frozen=True)
class TransportFastForward:
    pass

@dataclass(frozen=True)
class TransportRewind:
    pass

@dataclass(frozen=True)
class Seek:
    time_s: float


@dataclass(frozen=True)
class Jog:
    delta_degrees: float


@dataclass(frozen=True)
class JogStop:
    pass


@dataclass(frozen=True)
class Shutdown:
    pass


# =============================
# Events (backend -> UI)
# =============================


@dataclass(frozen=True)
class Loaded:
    duration_s: float
    sample_rate: int
    channels: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Playhead:
    time_s: float


@dataclass(frozen=True)
class Levels:
    rms_l: float
    rms_r: float


@dataclass(frozen=True)
class Status:
    text: str


# =============================
# Helpers
# =============================


def _setup_editor_logging(component: str) -> tuple[logging.Logger, str]:
    """Create a rotating file logger for the editor.

    Uses a single shared log file by default so UI + backend logs land together.
    Override with STEPD_EDITOR_LOG_PATH.
    """

    # Default to a stable file in the repo root (not dependent on CWD).
    # Use per-component files so the UI process and backend process don't rotate the same file.
    # Override with `STEPD_EDITOR_LOG_PATH` if you want a different location.
    log_path_env = os.environ.get("STEPD_EDITOR_LOG_PATH")
    log_path = None
    if log_path_env:
        try:
            safe_component = "".join(ch for ch in str(component) if ch.isalnum() or ch in ("_", "-")) or "backend"
            p = Path(str(log_path_env))
            # Treat as directory if it exists as a dir, or if it ends with a separator.
            if (p.exists() and p.is_dir()) or str(log_path_env).endswith(("/", "\\")):
                log_path = str((p / f"audio_editor_{safe_component}.log").resolve())
            else:
                log_path = str(p)
        except Exception:
            log_path = str(log_path_env)

    if not log_path:
        try:
            root_dir = Path(__file__).resolve().parents[1]
            safe_component = "".join(ch for ch in str(component) if ch.isalnum() or ch in ("_", "-")) or "backend"
            log_path = str((root_dir / f"audio_editor_{safe_component}.log").resolve())
        except Exception:
            safe_component = "".join(ch for ch in str(component) if ch.isalnum() or ch in ("_", "-")) or "backend"
            log_path = f"audio_editor_{safe_component}.log"

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
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s [%(processName)s:%(process)d] [%(threadName)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
        configured = True
    except Exception as e:
        # Last-ditch visibility: write setup failure to a simple text file.
        try:
            err_path = Path(__file__).resolve().parents[1] / "audio_editor_logging_errors.txt"
            err_path.write_text(f"Backend logger setup failed for {log_path}: {type(e).__name__}: {e}\n", encoding="utf-8")
        except Exception:
            pass

    logger.propagate = False
    return logger, log_path


def _append_editor_log_line(log_path: str, message: str) -> None:
    """Best-effort file append.

    Separate from `logging` so we can always create a log file even if handler
    setup is bypassed or fails.
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


def _db_to_linear(db: float) -> float:
    try:
        return float(10.0 ** (float(db) / 20.0))
    except Exception:
        return 1.0


class _PcmCache:
    """Decoded PCM store for sample-accurate playback.

    Backed by an mmap'd temp file when possible; falls back to
    multiprocessing.shared_memory when needed.

    Layout: float32 interleaved frames, shape (frames, channels).
    """

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int,
        frames_capacity: int,
        kind: str,
        buffer_obj: object,
        cleanup: "callable",
        path: Optional[str] = None,
        shm_name: Optional[str] = None,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.frames_capacity = int(max(0, frames_capacity))
        self.kind = str(kind)
        self.path = path
        self.shm_name = shm_name

        self._mv = memoryview(buffer_obj)
        self._cleanup = cleanup

        self.frames_written = 0
        self.frames_total = 0

    def close(self) -> None:
        try:
            self._cleanup()
        except Exception:
            pass

    def write_frames(self, start_frame: int, frames: np.ndarray) -> int:
        if frames is None or frames.size == 0:
            return 0
        if frames.ndim != 2:
            return 0
        if frames.shape[1] != self.channels:
            return 0

        start_frame = int(max(0, start_frame))
        n = int(frames.shape[0])
        if self.frames_capacity <= 0 or start_frame >= self.frames_capacity:
            return 0
        if start_frame + n > self.frames_capacity:
            n = int(max(0, self.frames_capacity - start_frame))
        if n <= 0:
            return 0

        byte_off = start_frame * self.channels * 4
        dst = np.frombuffer(self._mv, dtype=np.float32, count=n * self.channels, offset=byte_off)
        dst[:] = frames[:n, :].astype(np.float32, copy=False).reshape(-1)
        return n

    def read_into(self, outdata: np.ndarray, start_frame: int, n_frames: int) -> int:
        if outdata is None:
            return 0

        start_frame = int(max(0, start_frame))
        n_frames = int(max(0, n_frames))
        if n_frames <= 0:
            return 0

        available = int(self.frames_written) - start_frame
        if available <= 0:
            outdata[:n_frames, :] = 0
            return 0

        n = min(int(n_frames), int(available))
        byte_off = start_frame * self.channels * 4
        flat = np.frombuffer(self._mv, dtype=np.float32, count=n * self.channels, offset=byte_off)
        outdata[:n, :] = flat.reshape(n, self.channels)
        if n < n_frames:
            outdata[n:n_frames, :] = 0
        return int(n)


def update_jog_playback_speed(state: _BackendState) -> None:
    """Update jog playback speed from recent jog events (degrees/sec)."""
    if not state.jog_events:
        state.jog_playback_speed = 1.0
        return

    now = time.monotonic()
    # Calculate speed over the last 0.5 seconds
    window_start = now - 0.5
    recent_events = [(t, d) for t, d in state.jog_events if t >= window_start]

    if len(recent_events) < 2:
        state.jog_playback_speed = 1.0
        return

    # Total degrees and time span
    total_degrees = sum(d for t, d in recent_events)
    time_span = recent_events[-1][0] - recent_events[0][0]

    if time_span <= 0:
        state.jog_playback_speed = 1.0
        return

    degrees_per_sec = total_degrees / time_span

    # Map 360°/sec to 1.0x speed, clamp to reasonable range
    speed = degrees_per_sec / 360.0
    speed = max(-10.0, min(10.0, speed))

    # If speed is very small, set to 1.0 (no jog)
    if abs(speed) < 0.1:
        speed = 1.0

    state.jog_playback_speed = speed


class _BackendState:
    def __init__(self) -> None:
        self.lock = threading.Lock()

        self.file_path: Optional[str] = None
        self.output_device: Optional[int | str] = None

        self.target_sample_rate = 48000
        self.channels = 2
        self.duration_s: float = 0.0
        self.total_frames: int = 0

        self.in_s: float = 0.0
        self.out_s: Optional[float] = None

        self.gain_db: float = 0.0
        self.loop: bool = False

        self.playing: bool = False
        self.playhead_frame: int = 0

        # Scrub: play briefly after a jog
        self.scrub_until_monotonic: float = 0.0

        # Seek request (in frames, absolute)
        self.seek_frame: Optional[int] = None
        self.flush_requested: bool = False

        # Bump whenever stream format changes (e.g., channels) so worker threads can reset buffers.
        self.config_version: int = 0
        # Playback speed multiplier (1.0 = normal)
        self.playback_speed: float = 1.0
        # Playback direction: 1 for forward, -1 for reverse
        self.playback_direction: int = 1

        # Jog wheel tracking
        self.jog_events = []  # list of (timestamp, delta_degrees)
        self.jog_playback_speed: float = 1.0


def _get_cache_dir() -> Path:
    d = os.environ.get("STEPD_EDITOR_CACHE_DIR")
    if d:
        return Path(d)
    try:
        return Path(tempfile.gettempdir()) / "stepd_editor_cache"
    except Exception:
        return Path(".")


def start_editor_audio_backend() -> tuple[mp.Process, mp_connection.Connection, mp_connection.Connection]:
        """Spawn-safe starter.

        Returns (process, cmd_send_conn, evt_recv_conn)

        Design notes:
        - Uses Pipes (not mp.Queue) because mp.Queue can intermittently fail to deliver
            messages when a Qt event loop is running on Windows.
        - Backend event sending is buffered through an internal bounded queue + sender
            thread so GUI stalls cannot block playback.
        """

        ctx = mp.get_context("spawn")

        # Commands: UI sends -> backend receives
        cmd_recv, cmd_send = ctx.Pipe(duplex=False)
        # Events: backend sends -> UI receives
        evt_recv, evt_send = ctx.Pipe(duplex=False)

        proc = ctx.Process(
                target=_editor_backend_main,
                args=(cmd_recv, evt_send),
                daemon=False,
                name="EditorAudioBackend",
        )
        proc.start()
        return proc, cmd_send, evt_recv


def _safe_put(q: "queue.Queue[object]", msg: object) -> None:
    try:
        q.put_nowait(msg)
    except Exception:
        pass


def _extract_metadata(container) -> dict[str, Any]:
    md: dict[str, Any] = {}
    try:
        # PyAV container.metadata is dict-like
        for k, v in dict(getattr(container, "metadata", {}) or {}).items():
            if isinstance(k, str):
                md[k] = v
    except Exception:
        pass
    return md


def _editor_backend_main(cmd_conn: mp_connection.Connection, evt_conn: mp_connection.Connection) -> None:
    """Process entrypoint: owns OutputStream + PyAV decoder."""

    logger, log_path = _setup_editor_logging("backend")
    logger.info("Editor backend starting (log=%s)", log_path)
    _append_editor_log_line(log_path, "Editor backend starting")

    # Imports kept inside the process to avoid import-time side effects
    import av
    import sounddevice as sd

    state = _BackendState()

    # Fixed output format for editor playback.
    state.channels = 2

    pcm_cache: Optional[_PcmCache] = None
    pcm_cache_lock = threading.Lock()
    decode_stop_evt = threading.Event()
    decode_thread_obj: Optional[threading.Thread] = None

    stop_evt = threading.Event()

    # Event send buffer so GUI stalls never block decoder/audio.
    evt_q_local: "queue.Queue[object]" = queue.Queue(maxsize=2000)

    def evt_sender() -> None:
        while not stop_evt.is_set():
            try:
                msg = evt_q_local.get(timeout=0.1)
            except Exception:
                continue
            try:
                evt_conn.send(msg)
            except Exception:
                # If UI is gone or pipe is broken/full, drop.
                pass

    sender_thread = threading.Thread(target=evt_sender, name="EditorEvtSender", daemon=True)
    sender_thread.start()

    last_playhead_emit = 0.0
    last_levels_emit = 0.0

    # If PortAudio callback stalls (common on some Windows/CI setups), keep the UI
    # responsive by advancing playhead based on wallclock time.
    last_callback_t = 0.0
    play_ref_t = time.monotonic()
    play_ref_frame = 0

    # Shared counters for playhead/levels
    played_frames_since_last_levels = 0
    rms_accum = np.zeros((state.channels,), dtype=np.float64)

    def _reset_play_ref(now_monotonic: float) -> None:
        nonlocal play_ref_t, play_ref_frame
        play_ref_t = float(now_monotonic)
        try:
            with state.lock:
                play_ref_frame = int(state.playhead_frame)
        except Exception:
            play_ref_frame = 0

    def rebuild_stream() -> sd.OutputStream:
        try:
            device = None
            with state.lock:
                device = state.output_device
            # Log device info before opening stream
            logger.info(f"Attempting OutputStream open: device={device}")
            try:
                all_devices = sd.query_devices()
                logger.info(f"Available devices: {all_devices}")
            except Exception as dev_exc:
                logger.warning(f"Device enumeration failed: {type(dev_exc).__name__}: {dev_exc}")

            return sd.OutputStream(
                samplerate=state.target_sample_rate,
                channels=state.channels,
                dtype="float32",
                device=device,
                blocksize=1024,
                callback=audio_callback,
            )
        except Exception as e:
            logger.error(f"OutputStream open failed for device={device}: {type(e).__name__}: {e}")
            try:
                all_devices = sd.query_devices()
                logger.error(f"Available devices at failure: {all_devices}")
            except Exception as dev_exc:
                logger.warning(f"Device enumeration failed at failure: {type(dev_exc).__name__}: {dev_exc}")
            _safe_put(evt_q_local, Status(f"OutputStream open failed: {type(e).__name__}: {e}"))
            raise

    stream_lock = threading.Lock()
    stream_obj: Optional[sd.OutputStream] = None
    stream_starting = False
    stream_device_key: object = object()

    def _start_stream_async() -> None:
        nonlocal stream_obj, stream_starting, stream_device_key

        try:
            with state.lock:
                desired_device = state.output_device
        except Exception:
            desired_device = None

        with stream_lock:
            if stream_starting:
                return
            if stream_obj is not None and desired_device == stream_device_key:
                return
            stream_starting = True

        def worker() -> None:
            nonlocal stream_obj, stream_starting, stream_device_key

            try:
                # Swap out the old stream first.
                old = None
                with stream_lock:
                    old = stream_obj
                    stream_obj = None

                if old is not None:
                    try:
                        old.stop()
                    except Exception:
                        pass
                    try:
                        old.close()
                    except Exception:
                        pass

                s = rebuild_stream()
                s.start()
                with stream_lock:
                    stream_obj = s
                    stream_device_key = desired_device
            except Exception as e:
                _safe_put(evt_q_local, Status(f"OutputStream open failed: {type(e).__name__}: {e}"))
            finally:
                with stream_lock:
                    stream_starting = False

        threading.Thread(target=worker, name="EditorOutputStreamStart", daemon=True).start()

    def _close_cache() -> None:
        nonlocal pcm_cache
        with pcm_cache_lock:
            c = pcm_cache
            pcm_cache = None
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    def _create_cache(frames_capacity: int) -> _PcmCache:
        """Create an mmap-backed PCM cache, falling back to shared_memory."""

        frames_capacity = int(max(1, frames_capacity))
        byte_len = int(frames_capacity * state.channels * 4)
        if byte_len <= 0:
            raise RuntimeError("invalid cache size")

        cache_dir = _get_cache_dir()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # 1) Prefer mmap temp file.
        try:
            name = f"stepd_pcm_{os.getpid()}_{int(time.time() * 1000)}.f32"
            path = str((cache_dir / name).resolve())
            f = open(path, "w+b")
            try:
                f.truncate(byte_len)
                mm = mmap.mmap(f.fileno(), length=byte_len, access=mmap.ACCESS_WRITE)
            except Exception:
                try:
                    f.close()
                except Exception:
                    pass
                raise

            def cleanup() -> None:
                try:
                    mm.close()
                except Exception:
                    pass
                try:
                    f.close()
                except Exception:
                    pass
                try:
                    Path(path).unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    try:
                        Path(path).unlink()
                    except Exception:
                        pass

            return _PcmCache(
                sample_rate=state.target_sample_rate,
                channels=state.channels,
                frames_capacity=frames_capacity,
                kind="mmap",
                buffer_obj=mm,
                cleanup=cleanup,
                path=path,
            )
        except Exception as e:
            _safe_put(evt_q_local, Status(f"PCM cache mmap unavailable; using fallback ({type(e).__name__})"))

        # 2) shared_memory fallback
        from multiprocessing import shared_memory as mp_shared_memory

        shm = mp_shared_memory.SharedMemory(create=True, size=byte_len)

        def cleanup() -> None:
            try:
                shm.close()
            except Exception:
                pass
            try:
                shm.unlink()
            except Exception:
                pass

        return _PcmCache(
            sample_rate=state.target_sample_rate,
            channels=state.channels,
            frames_capacity=frames_capacity,
            kind="shared_memory",
            buffer_obj=shm.buf,
            cleanup=cleanup,
            shm_name=shm.name,
        )

    def _decode_into_cache(path: str, cache: _PcmCache, stop_event: threading.Event) -> None:
        """Decode entire file into the cache (best-effort)."""

        try:
            container = av.open(path)
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                _safe_put(evt_q_local, Status("No audio stream"))
                try:
                    container.close()
                except Exception:
                    pass
                return

            try:
                resampler = av.AudioResampler(format="fltp", layout="stereo", rate=cache.sample_rate)
            except Exception:
                resampler = av.AudioResampler(format="fltp", rate=cache.sample_rate)

            write_frame = 0
            last_status_t = time.monotonic()
            for packet in container.demux(stream):
                if stop_event.is_set() or stop_evt.is_set():
                    break
                for frame in packet.decode():
                    if stop_event.is_set() or stop_evt.is_set():
                        break
                    out_frames = resampler.resample(frame)
                    if not out_frames:
                        continue
                    for out in out_frames:
                        if stop_event.is_set() or stop_evt.is_set():
                            break
                        arr = out.to_ndarray()
                        if arr is None or arr.size == 0:
                            continue
                        if arr.ndim == 1:
                            arr = arr.reshape(1, -1)
                        if arr.shape[0] == 1:
                            arr = np.vstack([arr, arr])
                        elif arr.shape[0] > 2:
                            arr = arr[:2, :]

                        pcm = arr.T.astype(np.float32, copy=False)
                        wrote = cache.write_frames(write_frame, pcm)
                        if wrote <= 0:
                            _safe_put(evt_q_local, Status("PCM cache full (duration estimate too small?)"))
                            stop_event.set()
                            break

                        write_frame += int(wrote)
                        cache.frames_written = int(write_frame)

                        now = time.monotonic()
                        if now - last_status_t >= 0.5:
                            last_status_t = now
                            sec = float(write_frame) / float(cache.sample_rate)
                            _safe_put(evt_q_local, Status(f"Decoding PCM… {sec:.1f}s"))

            cache.frames_total = int(cache.frames_written)
            try:
                container.close()
            except Exception:
                pass
            _safe_put(evt_q_local, Status("PCM decoded"))
        except Exception as e:
            logger.exception("PCM decode failed")
            _safe_put(evt_q_local, Status(f"PCM decode failed: {type(e).__name__}: {e}"))

    def audio_callback(outdata, frames, time_info, status):
        nonlocal last_levels_emit, played_frames_since_last_levels, rms_accum, last_callback_t

        try:
            with state.lock:
                playing = bool(state.playing)
                gain_db = float(state.gain_db)
                loop = bool(state.loop)
                in_s = float(state.in_s)
                out_s = state.out_s
                total_frames = int(state.total_frames)
                playhead = int(state.playhead_frame)

                # End-of-scrub auto-pause
                if state.scrub_until_monotonic and time.monotonic() >= state.scrub_until_monotonic:
                    state.scrub_until_monotonic = 0.0
                    state.playing = False
                    playing = False

            if not playing:
                outdata[:] = 0
                return

            out_frames = int(frames)
            with pcm_cache_lock:
                cache = pcm_cache

            playback_speed = 1.0
            playback_direction = 1
            with state.lock:
                playback_speed = float(getattr(state, "playback_speed", 1.0))
                playback_direction = int(getattr(state, "playback_direction", 1))
                jog_speed = float(getattr(state, "jog_playback_speed", 1.0))
                # Jog overrides FF/RW if active
                if jog_speed != 1.0:
                    playback_speed = abs(jog_speed)
                    playback_direction = 1 if jog_speed >= 0 else -1

            # Read PCM data
            if cache is None:
                outdata[:out_frames, :] = 0
            else:
                try:
                    # Calculate number of source frames needed
                    src_frames = int(out_frames * playback_speed)
                    src = np.zeros((src_frames, cache.channels), dtype=np.float32)
                    cache.read_into(src, int(playhead), src_frames)

                    # Fast forward/rewind: resample and reverse if needed
                    if playback_speed != 1.0:
                        # Resample using numpy (simple linear interpolation)
                        idx = np.linspace(0, src_frames - 1, out_frames)
                        for ch in range(cache.channels):
                            outdata[:out_frames, ch] = np.interp(idx, np.arange(src_frames), src[:, ch])
                    else:
                        outdata[:out_frames, :] = src[:out_frames, :]

                    if playback_direction == -1:
                        outdata[:out_frames, :] = outdata[:out_frames, :][::-1]
                except Exception:
                    outdata[:out_frames, :] = 0

            # Apply gain
            g = _db_to_linear(gain_db)
            if g != 1.0:
                outdata[:out_frames, :] *= g

            # Clip
            np.clip(outdata, -1.0, 1.0, out=outdata)

            # Update playhead
            advance = int(out_frames * playback_speed) * playback_direction
            playhead_next = playhead + advance

            # In/Out-point enforcement (editor region).
            # When loop is off, stop exactly at the boundary and emit one final Playhead
            # so the UI shows the precise in/out point.
            in_frame = int(max(0.0, in_s) * state.target_sample_rate)
            out_frame: Optional[int] = None
            if out_s is not None:
                out_frame = int(max(0.0, float(out_s)) * state.target_sample_rate)

            hit_out = (out_frame is not None) and (playback_direction == 1) and (playhead_next >= int(out_frame))
            hit_in = (playback_direction == -1) and (playhead_next <= int(in_frame))
            if hit_out or hit_in:
                if loop and (out_frame is not None):
                    # Loop within the in/out region.
                    with state.lock:
                        state.playhead_frame = in_frame if hit_out else int(out_frame)
                    return

                # No loop: clamp and stop.
                with state.lock:
                    boundary = int(out_frame) if hit_out and out_frame is not None else int(in_frame)
                    state.playhead_frame = boundary
                    state.playing = False
                    # Reset jog state so further jog ticks can restart cleanly.
                    state.jog_events.clear()
                    state.jog_playback_speed = 1.0
                try:
                    _safe_put(evt_q_local, Playhead(time_s=float(boundary) / float(state.target_sample_rate)))
                except Exception:
                    pass
                return

            with state.lock:
                state.playhead_frame = playhead_next

            # Mark callback progress for the wallclock fallback.
            last_callback_t = time.monotonic()

            # Levels (RMS). Compute on the audio thread but publish from main loop.
            now = time.monotonic()
            if now - last_levels_emit >= 0.05:
                # rms per channel
                try:
                    x = outdata[:out_frames, :].astype(np.float64, copy=False)
                    rms = np.sqrt(np.mean(x * x, axis=0) + 1e-12)
                    rms_accum[:] = rms
                    played_frames_since_last_levels = frames
                    last_levels_emit = now
                except Exception:
                    pass

        except Exception:
            logger.exception("Audio callback error")
            try:
                outdata[:] = 0
            except Exception:
                pass

    try:
        _safe_put(evt_q_local, Status("Editor backend started"))

        logger.info("Backend started")
        _append_editor_log_line(log_path, "Backend started")

        # Start the stream lazily/asynchronously on first LoadFile/Play. This keeps
        # IPC responsive even if PortAudio device init blocks.

        while True:
            # Drain commands
            cmd = None
            try:
                if cmd_conn.poll(0.02):
                    cmd = cmd_conn.recv()
            except Exception:
                cmd = None

            if cmd is not None:
                if isinstance(cmd, Shutdown):
                    logger.info("Shutdown received")
                    _append_editor_log_line(log_path, "Shutdown received")
                    break

                if isinstance(cmd, LoadFile):
                    logger.info("LoadFile: %s", cmd.path)
                    _append_editor_log_line(log_path, f"LoadFile: {cmd.path}")

                    # Stop any previous decode and close its cache.
                    try:
                        decode_stop_evt.set()
                    except Exception:
                        pass
                    try:
                        if decode_thread_obj is not None and decode_thread_obj.is_alive():
                            decode_thread_obj.join(timeout=0.5)
                    except Exception:
                        pass
                    _close_cache()
                    decode_stop_evt = threading.Event()

                    with state.lock:
                        state.file_path = cmd.path
                        state.output_device = cmd.output_device
                        state.in_s = 0.0
                        state.out_s = None
                        state.playhead_frame = 0
                        state.playing = False
                        state.scrub_until_monotonic = 0.0
                        state.seek_frame = None
                        state.flush_requested = False
                        state.config_version += 1

                    try:
                        # Quick probe for duration + metadata so UI can size controls.
                        duration_s = 0.0
                        md: dict[str, Any] = {}
                        try:
                            c = av.open(cmd.path)
                            s = next((st for st in c.streams if st.type == "audio"), None)
                            if s is None:
                                logger.warning("No audio stream")
                                _safe_put(evt_q_local, Status("No audio stream"))
                                try:
                                    c.close()
                                except Exception:
                                    pass
                                continue

                            try:
                                if s.duration is not None and s.time_base is not None:
                                    duration_s = float(s.duration * s.time_base)
                            except Exception:
                                pass
                            if not duration_s:
                                try:
                                    if c.duration is not None:
                                        duration_s = float(c.duration / av.time_base)
                                except Exception:
                                    pass
                            duration_s = max(0.0, duration_s)
                            md = _extract_metadata(c)
                            try:
                                c.close()
                            except Exception:
                                pass
                        except Exception:
                            duration_s = 0.0
                            md = {}

                        frames_est = int(duration_s * state.target_sample_rate) if duration_s else 0
                        headroom = int(state.target_sample_rate * 2)
                        frames_cap = int(max(1, frames_est + headroom))

                        cache = _create_cache(frames_cap)
                        with pcm_cache_lock:
                            pcm_cache = cache

                        with state.lock:
                            state.duration_s = float(duration_s)
                            state.total_frames = int(frames_est) if frames_est else 0

                        # Ensure output stream is started for the selected device.
                        _start_stream_async()

                        _safe_put(
                            evt_q_local,
                            Loaded(
                                duration_s=float(duration_s),
                                sample_rate=state.target_sample_rate,
                                channels=state.channels,
                                metadata=md,
                            ),
                        )
                        _safe_put(evt_q_local, Status("Loaded"))
                        _safe_put(evt_q_local, Status("Decoding PCM cache"))

                        decode_thread_obj = threading.Thread(
                            target=_decode_into_cache,
                            args=(cmd.path, cache, decode_stop_evt),
                            name="EditorPcmDecode",
                            daemon=True,
                        )
                        decode_thread_obj.start()

                    except Exception as e:
                        logger.exception("Load failed")
                        _append_editor_log_line(log_path, f"Load failed: {type(e).__name__}: {e}")
                        _safe_put(evt_q_local, Status(f"Load failed: {type(e).__name__}: {e}"))
                        _close_cache()

                elif isinstance(cmd, SetInOut):
                    with state.lock:
                        state.in_s = max(0.0, float(cmd.in_s))
                        out_s = cmd.out_s
                        state.out_s = float(out_s) if out_s is not None else None

                elif isinstance(cmd, SetGain):
                    with state.lock:
                        state.gain_db = float(cmd.gain_db)

                elif isinstance(cmd, SetLoop):
                    with state.lock:
                        state.loop = bool(cmd.loop)

                elif isinstance(cmd, SetOutputDevice):
                    with state.lock:
                        state.output_device = cmd.output_device
                        state.config_version += 1
                    try:
                        _start_stream_async()
                    except Exception:
                        pass
                    try:
                        _safe_put(evt_q_local, Status(f"SetOutputDevice: {cmd.output_device}"))
                    except Exception:
                        pass

                elif isinstance(cmd, TransportPlay):
                    with state.lock:
                        state.playing = True
                        state.playback_speed = 1.0
                        state.playback_direction = 1
                    try:
                        last_callback_t = 0.0
                        _reset_play_ref(time.monotonic())
                    except Exception:
                        pass
                    try:
                        _start_stream_async()
                    except Exception:
                        pass
                    logger.debug("TransportPlay")
                    _safe_put(evt_q_local, Status("TransportPlay"))

                elif isinstance(cmd, TransportPause):
                    with state.lock:
                        state.playing = False
                        state.playback_speed = 0.0
                    try:
                        _reset_play_ref(time.monotonic())
                    except Exception:
                        pass
                    logger.debug("TransportPause")
                    _safe_put(evt_q_local, Status("TransportPause"))

                elif isinstance(cmd, TransportStop):
                    with state.lock:
                        state.playing = False
                        state.playback_speed = 0.0
                        state.playback_direction = 1
                        # stop resets to in-point
                        state.playhead_frame = int(max(0.0, state.in_s) * state.target_sample_rate)
                elif isinstance(cmd, TransportFastForward):
                    # Increase speed up to 10.0x, set direction forward
                    with state.lock:
                        state.playing = True
                        if state.playback_speed >= 0.0 and state.playback_direction == -1:
                            state.playback_speed = 0.5
                            state.playback_direction = 1
                        speed = float(getattr(state, "playback_speed", 1.0))
                        if speed < 10.0:
                            speed = min(10.0, round(speed + 0.5, 2))
                        state.playback_speed = speed
                        state.playback_direction = 1
                    try:
                        _reset_play_ref(time.monotonic())
                    except Exception:
                        pass
                    try:
                        _start_stream_async()
                    except Exception:
                        pass
                    logger.info(f"TransportFastForward: speed={speed}x")
                    _safe_put(evt_q_local, Status(f"FastForward: {speed}x"))

                elif isinstance(cmd, TransportRewind):
                    # Increase speed up to 10.0x, set direction reverse
                    with state.lock:
                        state.playing = True
                        if state.playback_speed >= 0.0 and state.playback_direction == 1:
                            state.playback_speed = 0.5
                            state.playback_direction = -1
                        speed = float(getattr(state, "playback_speed", 1.0))
                        if speed == 0.0:
                            speed = 0.5
                        if speed < 10.0:
                            speed = min(10.0, round(speed + 0.5, 2))
                        state.playback_speed = speed
                        state.playback_direction = -1
                    try:
                        _reset_play_ref(time.monotonic())
                    except Exception:
                        pass
                    try:
                        _start_stream_async()
                    except Exception:
                        pass
                    logger.info(f"TransportRewind: speed={speed}x")
                    _safe_put(evt_q_local, Status(f"Rewind: {speed}x"))

                elif isinstance(cmd, Seek):
                    logger.debug("Seek: %.3fs", float(cmd.time_s))
                    with state.lock:
                        target = max(0.0, float(cmd.time_s))
                        if state.duration_s > 0.0:
                            target = min(target, state.duration_s)
                        state.playhead_frame = int(target * state.target_sample_rate)
                    try:
                        _reset_play_ref(time.monotonic())
                    except Exception:
                        pass
                    

                elif isinstance(cmd, Jog):
                    logger.info("Jog: %.3f degrees", float(cmd.delta_degrees))
                    with state.lock:
                        now = time.monotonic()
                        state.jog_events.append((now, float(cmd.delta_degrees)))
                        # Keep only recent events (last 1 second)
                        cutoff = now - 1.0
                        state.jog_events = [(t, d) for t, d in state.jog_events if t >= cutoff]
                        # Update jog playback speed
                        update_jog_playback_speed(state)

                        # If this is the first jog tick (or the movement is very slow),
                        # update_jog_playback_speed may leave jog_speed at 1.0, which we use
                        # as a sentinel for "inactive". In that case, force a small jog speed
                        # so the very first notch visibly moves the playhead even from Pause.
                        try:
                            if float(state.jog_playback_speed) == 1.0 and float(cmd.delta_degrees) != 0.0:
                                state.jog_playback_speed = 0.25 if float(cmd.delta_degrees) > 0.0 else -0.25
                        except Exception:
                            pass

                        # Start playing if not already
                        start_stream = not state.playing
                        if start_stream:
                            state.playing = True
                    if start_stream:
                        # Prevent wallclock fallback from "catching up" using stale refs.
                        try:
                            _reset_play_ref(time.monotonic())
                        except Exception:
                            pass
                        try:
                            last_callback_t = 0.0
                        except Exception:
                            pass
                        try:
                            _start_stream_async()
                        except Exception:
                            pass

                elif isinstance(cmd, JogStop):
                    logger.info("JogStop")
                    with state.lock:
                        state.playing = False
                        state.jog_events.clear()
                        state.jog_playback_speed = 1.0

            # Emit playhead periodically.
            # Keep the rate modest to avoid filling the Pipe if the UI thread is busy.
            now = time.monotonic()

            # Wallclock fallback: if the audio callback isn't advancing playhead,
            # advance it here so the UI/playhead stays responsive.
            try:
                if now - float(last_callback_t) > 0.25:
                    with state.lock:
                        if state.playing:
                            sr = float(state.target_sample_rate)
                            # Respect effective speed + direction, including jog override.
                            eff_speed = float(getattr(state, "playback_speed", 1.0))
                            eff_dir = int(getattr(state, "playback_direction", 1))
                            try:
                                jog_speed = float(getattr(state, "jog_playback_speed", 1.0))
                                if jog_speed != 1.0:
                                    eff_speed = abs(jog_speed)
                                    eff_dir = 1 if jog_speed >= 0.0 else -1
                            except Exception:
                                pass

                            expected = int(play_ref_frame + (now - float(play_ref_t)) * sr * eff_speed * float(eff_dir))

                            # In/Out-point enforcement (same semantics as callback).
                            in_frame = int(max(0.0, float(state.in_s)) * sr)
                            out_s = state.out_s
                            out_frame: Optional[int] = None
                            if out_s is not None:
                                out_frame = int(max(0.0, float(out_s)) * sr)

                            hit_out = (out_frame is not None) and (eff_dir >= 0) and (expected >= int(out_frame))
                            hit_in = (eff_dir < 0) and (expected <= int(in_frame))
                            if hit_out or hit_in:
                                if state.loop and (out_frame is not None):
                                    boundary = in_frame if hit_out else int(out_frame)
                                    state.playhead_frame = int(boundary)
                                    play_ref_frame = int(boundary)
                                    play_ref_t = now
                                else:
                                    boundary = int(out_frame) if hit_out and out_frame is not None else int(in_frame)
                                    state.playhead_frame = int(boundary)
                                    state.playing = False
                                    # Reset jog state so further jog ticks can restart cleanly.
                                    state.jog_events.clear()
                                    state.jog_playback_speed = 1.0
                                    expected = int(boundary)
                                    try:
                                        _safe_put(evt_q_local, Playhead(time_s=float(boundary) / float(sr)))
                                    except Exception:
                                        pass
                            else:
                                # If no out-point is set, still clamp to start-of-file for safety.
                                if expected < 0:
                                    expected = 0

                            if state.playing:
                                cur = int(state.playhead_frame)
                                if eff_dir >= 0:
                                    if expected > cur:
                                        state.playhead_frame = expected
                                else:
                                    if expected < cur:
                                        state.playhead_frame = expected
            except Exception as e:
                try:
                    if os.environ.get("STEPD_EDITOR_DEBUG", "0") == "1":
                        logger.debug("Wallclock fallback failed: %s: %s", type(e).__name__, e)
                except Exception:
                    pass

            try:
                with state.lock:
                    playing_now = bool(state.playing)
                    t = float(state.playhead_frame) / float(state.target_sample_rate)
            except Exception:
                playing_now = False
                t = 0.0

            if playing_now and (now - last_playhead_emit >= 0.10):
                _safe_put(evt_q_local, Playhead(time_s=t))
                last_playhead_emit = now

            # Emit levels (best-effort)
            try:
                if played_frames_since_last_levels:
                    rms_l = float(rms_accum[0]) if rms_accum.size >= 1 else 0.0
                    rms_r = float(rms_accum[1]) if rms_accum.size >= 2 else rms_l
                    _safe_put(evt_q_local, Levels(rms_l=rms_l, rms_r=rms_r))
                    played_frames_since_last_levels = 0
            except Exception:
                pass

    finally:
        logger.info("Editor backend stopping")
        _append_editor_log_line(log_path, "Editor backend stopping")
        stop_evt.set()
        try:
            decode_stop_evt.set()
        except Exception:
            pass
        try:
            with stream_lock:
                s = stream_obj
                stream_obj = None
            if s is not None:
                try:
                    s.stop()
                except Exception:
                    pass
                try:
                    s.close()
                except Exception:
                    pass
        except Exception:
            pass
        _close_cache()
        try:
            _safe_put(evt_q_local, Status("Editor backend stopped"))
        except Exception:
            pass

        logger.info("Editor backend stopped")
        _append_editor_log_line(log_path, "Editor backend stopped")
