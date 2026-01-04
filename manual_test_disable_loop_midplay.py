#!/usr/bin/env python3
"""Manual/integration repro for: disabling loop mid-playback should NOT stop early.

What this does:
- Generates a short synthetic WAV.
- Starts AudioEngine.
- Plays the WAV with looping enabled.
- Disables looping shortly after start.
- Waits for CueFinishedEvent and prints timings.

Expected behavior (post-fix):
- The cue continues playing and finishes at the natural out point (EOF),
  not immediately when loop is disabled.

Run:
  venv/Scripts/python.exe manual_test_disable_loop_midplay.py
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import numpy as np
import av

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand
from engine.messages.events import CueFinishedEvent


def _create_test_wav(path: str, *, duration_s: float = 2.0, sample_rate: int = 48000) -> None:
    # Sine wave 440Hz mono
    t = np.linspace(0.0, duration_s, int(sample_rate * duration_s), endpoint=False)
    audio_f32 = (0.2 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)
    audio_i16 = (audio_f32 * 32767.0).astype(np.int16)

    container = av.open(path, "w")
    stream = container.add_stream("pcm_s16le", rate=sample_rate)

    # For packed audio (s16), PyAV expects shape (channels, samples)
    frame = av.AudioFrame.from_ndarray(audio_i16.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = sample_rate

    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def main() -> int:
    tmp_path = Path("manual_loop_disable_test.wav").absolute()
    if tmp_path.exists():
        try:
            tmp_path.unlink()
        except Exception:
            pass

    print(f"[SETUP] Creating test WAV: {tmp_path}")
    _create_test_wav(str(tmp_path), duration_s=2.0, sample_rate=48000)

    engine = AudioEngine(sample_rate=48000, channels=2, block_frames=2048)
    engine.start()

    cue_id = str(uuid.uuid4())
    cmd = PlayCueCommand(
        cue_id=cue_id,
        track_id="manual-test",
        file_path=str(tmp_path),
        in_frame=0,
        out_frame=None,
        gain_db=-6.0,
        loop_enabled=True,
        layered=True,
    )

    try:
        print(f"[PLAY] cue={cue_id[:8]} loop_enabled=True")
        t0 = time.perf_counter()
        engine.play_cue(cmd)

        # Let it start and buffer a bit.
        while time.perf_counter() - t0 < 0.25:
            engine.pump()
            time.sleep(0.01)

        t_disable = time.perf_counter()
        print(f"[UPDATE] Disabling loop at +{t_disable - t0:.3f}s")
        engine.update_cue(cue_id, loop_enabled=False)

        # Wait for finish.
        finished_evt: CueFinishedEvent | None = None
        deadline = time.perf_counter() + 10.0
        while time.perf_counter() < deadline:
            for evt in engine.pump():
                if isinstance(evt, CueFinishedEvent) and evt.cue_info.cue_id == cue_id:
                    finished_evt = evt
                    break
            if finished_evt is not None:
                break
            time.sleep(0.01)

        if finished_evt is None:
            print("[FAIL] Timed out waiting for CueFinishedEvent")
            return 2

        t_done = time.perf_counter()
        dt_after_disable = t_done - t_disable
        print(
            "[DONE] "
            f"finish_reason={finished_evt.reason} "
            f"finished_at=+{t_done - t0:.3f}s "
            f"after_disable={dt_after_disable:.3f}s"
        )

        # Heuristic: if disabling loop triggers an immediate stop bug, this would be near-zero.
        # We allow some slack for near-end timing.
        if dt_after_disable < 0.15:
            print("[FAIL] Finished too quickly after disabling loop (possible early-stop regression)")
            return 1

        print("[PASS] Loop disable did not stop cue immediately; finished naturally.")
        return 0

    finally:
        try:
            engine.stop()
        except Exception:
            pass
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
