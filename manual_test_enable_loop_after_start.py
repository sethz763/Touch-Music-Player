#!/usr/bin/env python3
"""Manual/integration test:
Pressing transport Loop AFTER a cue starts should make it loop.

This script:
- Generates a short WAV.
- Starts AudioEngine.
- Plays cue with loop_enabled=False.
- After a short delay, sends SetGlobalLoopEnabledCommand(enabled=True).
- Verifies the cue is still active well past the file duration (i.e., it looped).

Run:
  venv/Scripts/python.exe manual_test_enable_loop_after_start.py
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import numpy as np
import av

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand, SetGlobalLoopEnabledCommand
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
    wav_path = Path("manual_enable_loop_after_start.wav").absolute()
    if wav_path.exists():
        try:
            wav_path.unlink()
        except Exception:
            pass

    _create_test_wav(str(wav_path), duration_s=1.0, sample_rate=48000)

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
        print(f"[PLAY] cue={cue_id[:8]} loop_enabled=False")
        t0 = time.perf_counter()
        engine.play_cue(cmd)

        # Let playback start.
        while time.perf_counter() - t0 < 0.25:
            engine.pump()
            time.sleep(0.01)

        print(f"[TRANSPORT] Enabling global loop at +{time.perf_counter() - t0:.3f}s")
        engine.handle_command(SetGlobalLoopEnabledCommand(enabled=True))

        # If loop enable applies to active cue, it should still be active well beyond 1s.
        deadline = time.perf_counter() + 3.0
        finished = False
        while time.perf_counter() < deadline:
            for evt in engine.pump():
                if isinstance(evt, CueFinishedEvent) and evt.cue_info.cue_id == cue_id:
                    print(f"[FAIL] Cue finished early at +{time.perf_counter() - t0:.3f}s reason={evt.reason}")
                    finished = True
                    break
            if finished:
                break
            time.sleep(0.01)

        if finished:
            return 1

        active = cue_id in engine.active_cues
        print(f"[CHECK] at +{time.perf_counter() - t0:.3f}s active={active}")
        if not active:
            print("[FAIL] Cue is not active; loop enable after start did not take effect")
            return 2

        print("[PASS] Cue stayed active past natural duration (loop enable after start works)")
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
