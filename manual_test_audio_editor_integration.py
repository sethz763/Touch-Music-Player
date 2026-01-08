from __future__ import annotations

import math
import os
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np


def _write_test_wav(path: Path, *, seconds: float = 1.5, sample_rate: int = 48000) -> None:
    seconds = float(seconds)
    n = int(seconds * sample_rate)
    t = (np.arange(n, dtype=np.float32) / float(sample_rate)).reshape(-1, 1)

    # Simple stereo tone
    left = 0.20 * np.sin(2.0 * math.pi * 440.0 * t)
    right = 0.20 * np.sin(2.0 * math.pi * 660.0 * t)
    pcm = np.concatenate([left, right], axis=1)

    # int16 PCM
    pcm_i16 = np.clip(pcm, -1.0, 1.0)
    pcm_i16 = (pcm_i16 * 32767.0).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16.tobytes())


def main() -> int:
    # Ensure spawn-safe on Windows.
    try:
        import multiprocessing as mp

        mp.freeze_support()
        mp.set_start_method("spawn", force=True)
    except Exception:
        pass

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QWidget

    from ui.widgets.sound_file_button import SoundFileButton

    tmp_dir = Path(tempfile.gettempdir()) / "stepd_editor_manual"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wav_path = tmp_dir / "editor_integration_test.wav"
    _write_test_wav(wav_path)

    app = QApplication([])

    host = QWidget()
    host.setWindowTitle("Editor integration harness")

    btn = SoundFileButton("Test", file_path=str(wav_path), engine_adapter=None)
    btn.setParent(host)

    # Give the button a stable track_id label.
    btn.bank_index = 0
    btn.index_in_bank = 1

    # Seed metadata/params
    btn.sample_rate = 48000
    btn.duration_seconds = 1.5
    btn.in_frame = int(0.10 * 48000)
    btn.out_frame = int(1.10 * 48000)
    btn.gain_db = -6.0
    btn.loop_enabled = False

    # Open the editor programmatically.
    btn._open_editor()

    # Fetch the window that SoundFileButton keeps referenced.
    try:
        win = btn._audio_editor_window
    except Exception:
        print("FAILED: editor window not created")
        return 2

    # After a short delay, modify model and close.
    def apply_edits_and_close() -> None:
        try:
            win._model.in_point_s = 0.25
            win._model.out_point_s = 1.00
            win._model.gain_db = -12.0
            win._model.loop_enabled = True
            win._model.duration_s = 1.5
            win._model.metadata = {"title": "Integration Test", "artist": "StepD"}
        except Exception as e:
            print(f"FAILED: could not set model fields: {e}")
            app.quit()
            return

        # Trigger close; this emits cue_edits_committed -> SoundFileButton updates.
        win.close()

    def verify() -> None:
        # Verify SoundFileButton received the commit.
        ok = True
        try:
            if abs(btn.gain_db - (-12.0)) > 1e-6:
                print(f"FAILED: gain_db {btn.gain_db}")
                ok = False
            if btn.loop_enabled is not True:
                print(f"FAILED: loop_enabled {btn.loop_enabled}")
                ok = False

            if btn.in_frame != int(0.25 * 48000):
                print(f"FAILED: in_frame {btn.in_frame}")
                ok = False
            if btn.out_frame != int(1.00 * 48000):
                print(f"FAILED: out_frame {btn.out_frame}")
                ok = False

            if btn.duration_seconds is None or abs(btn.duration_seconds - 1.5) > 1e-6:
                print(f"FAILED: duration_seconds {btn.duration_seconds}")
                ok = False

            if btn.song_title != "Integration Test":
                print(f"FAILED: song_title {btn.song_title}")
                ok = False
            if btn.song_artist != "StepD":
                print(f"FAILED: song_artist {btn.song_artist}")
                ok = False

        except Exception as e:
            print(f"FAILED: verify exception: {e}")
            ok = False

        if ok:
            print("OK: editor commit updated SoundFileButton state")
            app.exit(0)
        else:
            app.exit(3)

    QTimer.singleShot(300, apply_edits_and_close)
    QTimer.singleShot(800, verify)

    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
