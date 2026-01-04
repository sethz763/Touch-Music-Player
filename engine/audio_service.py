"""
AudioService: Independent audio engine process isolated from Qt GUI.

Runs in a separate process to ensure audio playback continues uninterrupted
even when Qt native file dialogs or other blocking operations stall the main GUI thread.

The service communicates with the GUI via multiprocessing queues:
- cmd_q: GUI sends commands (PlayCueCommand, StopCueCommand, etc.)
- evt_q: Service sends events back to GUI (CueFinishedEvent, DecodeErrorEvent, etc.)
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from typing import Optional

from engine.audio_engine import AudioEngine
from engine.commands import (
    PlayCueCommand,
    StopCueCommand,
    TransportNext, 
    TransportPrev, 
    TransportPlay, 
    TransportPause, 
    FadeCueCommand, 
    SetAutoFadeCommand, 
    UpdateCueCommand,
    SetMasterGainCommand,
    TransportStop,
    BatchCommandsCommand,
    OutputSetDevice,
    OutputSetConfig,
    OutputListDevices,
    SetTransitionFadeDurations,
)
from engine.messages.events import (
    CueStartedEvent,
    CueFinishedEvent, 
    DecodeErrorEvent,     
    TransportStateEvent,
    CueTimeEvent
)


@dataclass(frozen=True, slots=True)
class AudioServiceConfig:
    """Audio service configuration."""
    sample_rate: int = 48000
    channels: int = 2
    block_frames: int = 2048
    fade_in_ms: int = 100
    fade_out_ms: int = 1000
    fade_curve: str = "equal_power"
    auto_fade_on_new: bool = True
    pump_interval_ms: float = 5.0  # How often to call engine.pump()

    # Crash-safety: if the GUI process dies, the service should not remain running.
    # This avoids orphaned background processes on app crashes.
    parent_pid: Optional[int] = None
    parent_watchdog_enabled: bool = True
    parent_watchdog_poll_s: float = 0.5


def audio_service_main(
    cmd_q: mp.Queue,
    evt_q: mp.Queue,
    config: AudioServiceConfig,
) -> None:
    """
    Main loop for the audio service process.
    
    Runs independently of Qt, continuously:
    1. Drains incoming commands from cmd_q (non-blocking)
    2. Routes commands to audio engine
    3. Calls engine.pump() to process events
    4. Forwards engine events to evt_q for GUI consumption
    5. Sleeps briefly to yield CPU
    
    Stops when cmd_q receives None.
    """
    try:
        # Capture the parent PID (GUI) and watch it so we can self-terminate
        # if the GUI process crashes or is killed.
        parent_pid = int(config.parent_pid) if config.parent_pid else int(os.getppid())

        def _is_parent_alive(pid: int) -> bool:
            try:
                if pid <= 0:
                    return False
                if os.name == "nt":
                    import ctypes
                    from ctypes import wintypes

                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    SYNCHRONIZE = 0x00100000
                    WAIT_OBJECT_0 = 0x00000000
                    WAIT_TIMEOUT = 0x00000102

                    kernel32 = ctypes.windll.kernel32
                    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                    kernel32.OpenProcess.restype = wintypes.HANDLE
                    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                    kernel32.WaitForSingleObject.restype = wintypes.DWORD
                    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                    kernel32.CloseHandle.restype = wintypes.BOOL

                    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
                    if not handle:
                        return False
                    try:
                        rc = kernel32.WaitForSingleObject(handle, 0)
                        if rc == WAIT_TIMEOUT:
                            return True
                        if rc == WAIT_OBJECT_0:
                            return False
                        # Unknown: assume dead to be safe.
                        return False
                    finally:
                        kernel32.CloseHandle(handle)

                # POSIX: os.kill(pid, 0) checks existence.
                try:
                    os.kill(pid, 0)
                    return True
                except Exception:
                    return False
            except Exception:
                return True  # best-effort; don't kill audio on watchdog errors

        # Create and start the audio engine with the provided config
        engine = AudioEngine(
            sample_rate=config.sample_rate,
            channels=config.channels,
            block_frames=config.block_frames,
            fade_in_ms=config.fade_in_ms,
            fade_out_ms=config.fade_out_ms,
            fade_curve=config.fade_curve,
            auto_fade_on_new=config.auto_fade_on_new,
        )
        engine.start()
        
        # Main service loop
        pump_interval = config.pump_interval_ms / 1000.0
        running = True
        pump_count = 0
        next_watchdog_check = time.monotonic() + float(getattr(config, "parent_watchdog_poll_s", 0.5) or 0.5)

        while running:
            try:
                # Parent watchdog (prevents orphaned processes on GUI crash)
                if getattr(config, "parent_watchdog_enabled", True):
                    now_mono = time.monotonic()
                    if now_mono >= next_watchdog_check:
                        next_watchdog_check = now_mono + float(getattr(config, "parent_watchdog_poll_s", 0.5) or 0.5)
                        if not _is_parent_alive(parent_pid):
                            running = False
                            break

                # Drain commands from GUI (non-blocking)
                while True:
                    try:
                        cmd = cmd_q.get_nowait()
                    except Exception:
                        break

                    # Shutdown signals
                    if cmd is None:
                        running = False
                        break

                    # BatchCommandsCommand - unwrap and process each command atomically
                    if isinstance(cmd, BatchCommandsCommand):
                        try:
                            for batched_cmd in cmd.commands:
                                if isinstance(batched_cmd, PlayCueCommand):
                                    try:
                                        cue_started_event = engine.play_cue(batched_cmd)
                                        if cue_started_event:
                                            try:
                                                evt_q.put_nowait(cue_started_event)
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                else:
                                    # Other commands (StopCueCommand, FadeCueCommand, UpdateCueCommand)
                                    try:
                                        engine.handle_command(batched_cmd)
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        continue

                    # PlayCueCommand is special - route to play_cue()
                    if isinstance(cmd, PlayCueCommand):
                        try:
                            cue_started_event = engine.play_cue(cmd)
                            # Queue the CueStartedEvent immediately to GUI
                            if cue_started_event:
                                try:
                                    evt_q.put_nowait(cue_started_event)
                                except Exception as queue_err:
                                    pass
                        except Exception as e:
                            pass
                    else:
                        # Route all other commands to handle_command()
                        try:
                            engine.handle_command(cmd)
                        except Exception:
                            pass

                # Process engine events (includes cue_started from play_cue)
                if running:
                    try:
                        events = engine.pump()
                        pump_count += 1
                        for evt in events:
                            try:
                                evt_q.put_nowait(evt)
                            except Exception as e:
                                pass
                    except Exception as e:
                        pass

                # Small sleep to yield CPU and prevent busy-waiting
                time.sleep(pump_interval)

            except Exception as e:
                # Catch any unexpected errors in main loop
                # Log them if possible, but keep running
                pass

        # Shutdown: stop the engine
        try:
            engine.stop()
        except Exception:
            pass

    except Exception as e:
        # Fatal error during initialization or main loop
        pass
