#!/usr/bin/env python3
"""Manual/integration test:
Disable loop override while a cue is looping; it should stop cleanly at the next loop boundary.

We force the cue to loop via: global loop ON + loop override ON.
Then we disable looping by setting global loop OFF (while override is still ON),
then turn override OFF.

Expected:
- Cue finishes within a short time window (no extra full loop).

Run:
  venv/Scripts/python.exe manual_test_disable_override_stops_cleanly.py
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import numpy as np
import av

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand, SetGlobalLoopEnabledCommand, SetLoopOverrideCommand
from engine.messages.events import CueFinishedEvent


def _create_test_wav(path: str, *, duration_s: float = 1.0, sample_rate: int = 48000) -> None:
    t = np.linspace(0.0, duration_s, int(sample_rate * duration_s), endpoint=False)
    audio_f32 = (0.2 * np.sin(2.0 * np.pi * 220.0 * t)).astype(np.float32)
    audio_i16 = (audio_f32 * 32767.0).astype(np.int16)

    container = av.open(path, "w")
    stream = container.add_stream("pcm_s16le", rate=sample_rate)
    frame = av.AudioFrame.from_ndarray(audio_i16.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = sample_rate

    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def main() -> int:
    wav_path = Path("manual_disable_override_stops_cleanly.wav").absolute()
    if wav_path.exists():
        try:
            wav_path.unlink()
        except Exception:
            pass

    duration_s = 1.0
    _create_test_wav(str(wav_path), duration_s=duration_s, sample_rate=48000)

    engine = AudioEngine(sample_rate=48000, channels=2, block_frames=2048)
    engine.start()

    cue_id = str(uuid.uuid4())
    cmd = PlayCueCommand(
        cue_id=cue_id,
        track_id="manual-test",
        file_path=str(wav_path),
        in_frame=0,
        out_frame=None,
        gain_db=-6.0,
        loop_enabled=False,
        layered=True,
    )

    try:
        t0 = time.perf_counter()
        print(f"[PLAY] cue={cue_id[:8]} loop_enabled=False")
        engine.play_cue(cmd)

        # Let playback start.
        while time.perf_counter() - t0 < 0.25:
            engine.pump()
            time.sleep(0.01)

        print(f"[TRANSPORT] global loop ON at +{time.perf_counter() - t0:.3f}s")
        engine.handle_command(SetGlobalLoopEnabledCommand(enabled=True))

        time.sleep(0.05)
        print(f"[TRANSPORT] override ON at +{time.perf_counter() - t0:.3f}s")
        engine.handle_command(SetLoopOverrideCommand(enabled=True))

        # Give it enough time to be safely past the first natural duration.
        # If looping works, cue should remain active.
        deadline_loop_check = time.perf_counter() + 1.5
        while time.perf_counter() < deadline_loop_check:
            engine.pump()
            time.sleep(0.01)

        if cue_id not in engine.active_cues:
            print("[FAIL] Cue is not active; it did not loop under override")
            return 1

        disable_t = time.perf_counter()
        print(f"[TRANSPORT] global loop OFF at +{disable_t - t0:.3f}s")
        engine.handle_command(SetGlobalLoopEnabledCommand(enabled=False))

        time.sleep(0.05)
        print(f"[TRANSPORT] override OFF at +{time.perf_counter() - t0:.3f}s")
        engine.handle_command(SetLoopOverrideCommand(enabled=False))

        # After disabling looping, cue should finish within ~one duration (+ buffer slack).
        finished_evt: CueFinishedEvent | None = None
        finish_deadline = time.perf_counter() + 3.0
        while time.perf_counter() < finish_deadline:
            for evt in engine.pump():
                if isinstance(evt, CueFinishedEvent) and evt.cue_info.cue_id == cue_id:
                    finished_evt = evt
                    break
            if finished_evt is not None:
                break
            time.sleep(0.01)

        if finished_evt is None:
            print("[FAIL] Cue did not finish within 3s after disabling looping")
            return 2

        dt = time.perf_counter() - disable_t
        print(f"[FINISH] +{time.perf_counter() - t0:.3f}s reason={finished_evt.reason} dt_since_disable={dt:.3f}s")

        if cue_id in engine.active_cues:
            print("[FAIL] Cue still active after finish event")
            return 3

        # Heuristic: should not keep playing for multiple extra durations.
        if dt > (duration_s * 2.5):
            print("[FAIL] Took too long to finish after disabling looping (possible extra loop)")
            return 4

        print("[PASS] Finished promptly after disabling looping (clean boundary stop)")
        return 0

    finally:
        try:
            engine.stop()
        except Exception:
            pass
        try:
            if wav_path.exists():
                wav_path.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
