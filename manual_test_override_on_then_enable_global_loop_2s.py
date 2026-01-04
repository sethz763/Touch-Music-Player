#!/usr/bin/env python3
"""Manual/integration test:
Scenario matching GUI report:
- loop override is ON
- cue starts with global loop OFF
- while cue is playing, user enables global loop button

Expected:
- Cue begins looping after it reaches its outpoint.
- When global loop is later disabled, cue finishes at next outpoint (not immediately).

Run:
  venv/Scripts/python.exe manual_test_override_on_then_enable_global_loop_2s.py
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import numpy as np
import av

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand, SetGlobalLoopEnabledCommand, SetLoopOverrideCommand
from engine.messages.events import CueFinishedEvent, BatchCueTimeEvent


def _create_test_wav(path: str, *, duration_s: float = 2.0, sample_rate: int = 48000) -> None:
    t = np.linspace(0.0, duration_s, int(sample_rate * duration_s), endpoint=False)
    tone = (0.15 * np.sin(2.0 * np.pi * 220.0 * t)).astype(np.float32)
    tone[:200] += 0.6
    tone = np.clip(tone, -1.0, 1.0)
    audio_i16 = (tone * 32767.0).astype(np.int16)

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
    sample_rate = 48000
    duration_s = 2.0
    out_frame = int(sample_rate * duration_s)

    wav_path = Path("manual_override_on_then_enable_global_loop_2s.wav").absolute()
    if wav_path.exists():
        try:
            wav_path.unlink()
        except Exception:
            pass

    _create_test_wav(str(wav_path), duration_s=duration_s, sample_rate=sample_rate)

    engine = AudioEngine(sample_rate=sample_rate, channels=2, block_frames=2048)
    engine.start()

    cue_id = str(uuid.uuid4())
    cmd = PlayCueCommand(
        cue_id=cue_id,
        track_id="manual-test",
        file_path=str(wav_path),
        in_frame=0,
        out_frame=out_frame,
        gain_db=-6.0,
        loop_enabled=False,
        layered=True,
    )

    try:
        t0 = time.perf_counter()
        print(f"[OVERRIDE] ON before play")
        engine.handle_command(SetLoopOverrideCommand(enabled=True))
        engine.handle_command(SetGlobalLoopEnabledCommand(enabled=False))

        print(f"[PLAY] cue={cue_id[:8]} out_frame={out_frame} ({duration_s}s)")
        engine.play_cue(cmd)

        # Wait until we see some time telemetry for the cue (confirms audio started).
        last_elapsed = None
        started_deadline = time.perf_counter() + 3.0
        while time.perf_counter() < started_deadline:
            for evt in engine.pump():
                if isinstance(evt, BatchCueTimeEvent):
                    tinfo = evt.cue_times.get(cue_id)
                    if tinfo:
                        last_elapsed = float(tinfo[0])
                        break
            if last_elapsed is not None and last_elapsed > 0.2:
                break
            time.sleep(0.01)

        print(f"[GLOBAL LOOP] ON while playing at +{time.perf_counter() - t0:.3f}s")
        engine.handle_command(SetGlobalLoopEnabledCommand(enabled=True))

        # Confirm it does NOT finish at the first outpoint.
        deadline = time.perf_counter() + (duration_s + 1.0)
        while time.perf_counter() < deadline:
            for evt in engine.pump():
                if isinstance(evt, CueFinishedEvent) and evt.cue_info.cue_id == cue_id:
                    print(f"[FAIL] Finished at first outpoint even though global loop ON (override ON). reason={evt.reason}")
                    return 1
            time.sleep(0.01)

        if cue_id not in engine.active_cues:
            print("[FAIL] Cue not active after >1 duration; did not start looping")
            return 2

        disable_t = time.perf_counter()
        print(f"[GLOBAL LOOP] OFF at +{disable_t - t0:.3f}s")
        engine.handle_command(SetGlobalLoopEnabledCommand(enabled=False))

        # Should finish at next outpoint (not immediately).
        immediate_deadline = time.perf_counter() + 0.2
        while time.perf_counter() < immediate_deadline:
            for evt in engine.pump():
                if isinstance(evt, CueFinishedEvent) and evt.cue_info.cue_id == cue_id:
                    dt = time.perf_counter() - disable_t
                    print(f"[FAIL] Finished immediately after global loop OFF dt={dt:.3f}s")
                    return 3
            time.sleep(0.01)

        finish_deadline = time.perf_counter() + 3.0
        finished_evt = None
        while time.perf_counter() < finish_deadline:
            for evt in engine.pump():
                if isinstance(evt, CueFinishedEvent) and evt.cue_info.cue_id == cue_id:
                    finished_evt = evt
                    break
            if finished_evt is not None:
                break
            time.sleep(0.01)

        if finished_evt is None:
            print("[FAIL] Did not finish within 3s after global loop OFF")
            return 4

        dt = time.perf_counter() - disable_t
        print(f"[PASS] Finished after global loop OFF dt={dt:.3f}s reason={finished_evt.reason}")
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
