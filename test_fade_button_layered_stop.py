import os
import time
import queue
import uuid
from pathlib import Path

import numpy as np
import av

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand, StopCueCommand


def _create_test_wav(path: Path, duration_seconds: float = 0.25, sample_rate: int = 48000) -> None:
    # Simple 440Hz sine wave, mono, pcm_s16le.
    t = np.linspace(0, duration_seconds, int(sample_rate * duration_seconds), False)
    audio = np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    audio_int16 = (audio * 32767).astype(np.int16)

    container = av.open(str(path), "w")
    stream = container.add_stream("pcm_s16le", rate=sample_rate)
    frame = av.AudioFrame.from_ndarray(audio_int16.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = sample_rate

    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _drain(q) -> list:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except (queue.Empty, Exception):
            break
    return out


def _drain_wait(q, *, timeout_s: float = 0.25) -> list:
    """Drain a multiprocessing.Queue with a short wait to allow feeder-thread flush."""
    deadline = time.time() + float(timeout_s)
    out: list = []
    while time.time() < deadline:
        chunk = _drain(q)
        if chunk:
            out.extend(chunk)
            # Continue draining until empty, but don't spin forever.
            continue
        time.sleep(0.01)
    # Final drain
    out.extend(_drain(q))
    return out


def test_fade_stop_dispatches_for_all_layered_instances(tmp_path: Path):
    # This is a pure-engine regression: we don't start subprocesses or touch sounddevice.
    # We only verify that a StopCueCommand with fade_out_ms results in an actual stop being dispatched
    # after the fade duration elapses.
    wav_path = tmp_path / "fade_btn_layered.wav"
    _create_test_wav(wav_path)

    engine = AudioEngine(sample_rate=48000, channels=2, block_frames=1024, fade_in_ms=0, fade_out_ms=200)

    cue_ids = [uuid.uuid4().hex, uuid.uuid4().hex]
    for cue_id in cue_ids:
        cmd = PlayCueCommand(
            cue_id=cue_id,
            file_path=str(wav_path),
            track_id=None,
            gain_db=0.0,
            in_frame=0,
            out_frame=None,
            fade_in_ms=0,
            loop_enabled=False,
            layered=True,
            total_seconds=None,
        )
        engine.play_cue(cmd, layered=True)

    # Sanity: both cues are tracked as active.
    assert all(cid in engine.active_cues for cid in cue_ids)

    # Clear any startup commands so assertions are focused.
    _drain(engine._decode_cmd_q)
    _drain(engine._out_cmd_q)

    fade_ms = 50
    for cid in cue_ids:
        engine.handle_command(StopCueCommand(cue_id=cid, fade_out_ms=fade_ms))

    # FadeTo should be queued immediately (stop comes later).
    out_now = _drain_wait(engine._out_cmd_q)
    assert out_now, "Expected OutputFadeTo commands to be queued"

    # After fade duration passes, pump() should dispatch DecodeStop and OutputStopCue for each cue.
    time.sleep((fade_ms / 1000.0) + 0.05)
    engine.pump()

    decode_cmds = _drain_wait(engine._decode_cmd_q)
    out_cmds = _drain_wait(engine._out_cmd_q)

    decode_stop_ids = {getattr(m, "cue_id", None) for m in decode_cmds if type(m).__name__ == "DecodeStop"}
    out_stop_ids = {getattr(m, "cue_id", None) for m in out_cmds if type(m).__name__ == "OutputStopCue"}

    assert set(cue_ids).issubset(decode_stop_ids)
    assert set(cue_ids).issubset(out_stop_ids)
