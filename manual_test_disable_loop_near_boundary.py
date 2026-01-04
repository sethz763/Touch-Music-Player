#!/usr/bin/env python3
"""Manual test: disable loop very close to the loop boundary.

This targets the reported behavior:
- short cue (e.g. 2s) can glitch (brief stop/repeat samples)
- longer cue can accidentally play a full extra loop after loop-off

We create a synthetic WAV of a chosen duration, start it looped, then
disable looping near the end of the first iteration.

Run:
  venv/Scripts/python.exe manual_test_disable_loop_near_boundary.py
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import numpy as np
import av

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand
from engine.messages.events import CueFinishedEvent


def _create_test_wav(path: str, *, duration_s: float, sample_rate: int = 48000) -> None:
    t = np.linspace(0.0, duration_s, int(sample_rate * duration_s), endpoint=False)
    # add a sharp transient each 0.25s to make repeats/glitches obvious
    base = 0.15 * np.sin(2.0 * np.pi * 330.0 * t)
    clicks = np.zeros_like(base)
    step = int(sample_rate * 0.25)
    clicks[::step] = 0.9
    audio_f32 = (base + clicks).astype(np.float32)
    audio_f32 = np.clip(audio_f32, -1.0, 1.0)
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


def run_case(duration_s: float, disable_at_s: float) -> int:
    wav_path = Path(f"manual_loop_disable_near_boundary_{duration_s:.1f}s.wav").absolute()
    if wav_path.exists():
        try:
            wav_path.unlink()
        except Exception:
            pass

    _create_test_wav(str(wav_path), duration_s=duration_s)

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
        loop_enabled=True,
        layered=True,
    )

    try:
        print(f"\n[CASE] duration={duration_s:.2f}s disable_at={disable_at_s:.2f}s cue={cue_id[:8]}")
        t0 = time.perf_counter()
        engine.play_cue(cmd)

        # wait until disable time
        while time.perf_counter() - t0 < disable_at_s:
            engine.pump()
            time.sleep(0.005)

        print(f"[UPDATE] disabling loop at +{time.perf_counter() - t0:.3f}s")
        engine.update_cue(cue_id, loop_enabled=False)

        finished_evt = None
        deadline = time.perf_counter() + 10.0
        while time.perf_counter() < deadline:
            for evt in engine.pump():
                if isinstance(evt, CueFinishedEvent) and evt.cue_info.cue_id == cue_id:
                    finished_evt = evt
                    break
            if finished_evt is not None:
                break
            time.sleep(0.005)

        if finished_evt is None:
            print("[FAIL] timed out waiting for finish")
            return 2

        t_done = time.perf_counter()
        print(f"[DONE] reason={finished_evt.reason} finished_at=+{t_done - t0:.3f}s")
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


def main() -> int:
    # disable close to boundary to stress race
    # 2s clip: disable at 1.85s
    # 4s clip: disable at 3.85s
    rc1 = run_case(2.0, 1.85)
    rc2 = run_case(4.0, 3.85)
    return 0 if (rc1 == 0 and rc2 == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
