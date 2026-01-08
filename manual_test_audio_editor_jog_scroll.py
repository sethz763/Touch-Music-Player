from __future__ import annotations

import math
import faulthandler
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np


def _write_test_wav(path: Path, *, seconds: float = 6.0, sample_rate: int = 48000) -> None:
    seconds = float(seconds)
    n = int(seconds * sample_rate)
    t = (np.arange(n, dtype=np.float32) / float(sample_rate)).reshape(-1, 1)

    left = 0.20 * np.sin(2.0 * math.pi * 440.0 * t)
    right = 0.20 * np.sin(2.0 * math.pi * 660.0 * t)
    pcm = np.concatenate([left, right], axis=1)

    pcm_i16 = np.clip(pcm, -1.0, 1.0)
    pcm_i16 = (pcm_i16 * 32767.0).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16.tobytes())


def main() -> int:
    try:
        import multiprocessing as mp

        mp.freeze_support()
        mp.set_start_method("spawn", force=True)
    except Exception:
        pass

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest

    from ui.windows.audio_editor_window import AudioEditorWindow

    tmp_dir = Path(tempfile.gettempdir()) / "stepd_editor_manual"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wav_path = tmp_dir / "editor_jog_scroll_test.wav"
    _write_test_wav(wav_path)

    print("[jog_scroll] creating QApplication", file=sys.stderr, flush=True)
    app = QApplication([])
    print("[jog_scroll] QApplication created", file=sys.stderr, flush=True)

    # If the UI thread hangs (e.g., event loop starved), dump Python stack traces.
    try:
        hang_f = Path("editor_jog_scroll_hang.txt").open("w", encoding="utf-8")
        faulthandler.enable(file=hang_f)
        faulthandler.dump_traceback_later(3.0, repeat=False, file=hang_f)
    except Exception:
        hang_f = None

    print("[jog_scroll] constructing AudioEditorWindow", file=sys.stderr, flush=True)
    win = AudioEditorWindow(
        file_path=str(wav_path),
        track_id="jog-scroll-test",
        in_point_s=0.0,
        out_point_s=5.5,
        gain_db=-6.0,
        loop_enabled=False,
        parent=None,
    )
    print("[jog_scroll] AudioEditorWindow constructed", file=sys.stderr, flush=True)
    win.show()
    print("[jog_scroll] window shown", file=sys.stderr, flush=True)

    # Ensure there is horizontal scroll range even before waveform load completes.
    try:
        win.waveform.setFixedWidth(5000)
    except Exception:
        pass

    start_frame = {"v": None}
    end_frame = {"v": None}

    def run_steps() -> None:
        # Give some time for backend to start emitting playhead.
        QTest.qWait(400)

        # Start playback briefly.
        try:
            from engine.editor_audio_service import TransportPlay

            win._send(TransportPlay())
        except Exception:
            pass

        # Wait until playhead advances (up to ~2s).
        start = 0
        try:
            start = int(getattr(win.waveform, "position_frame", 0))
        except Exception:
            start = 0
        start_frame["v"] = start

        advanced = False
        for _ in range(40):
            QTest.qWait(50)
            try:
                cur = int(getattr(win.waveform, "position_frame", 0))
            except Exception:
                cur = 0
            if cur > start:
                advanced = True
                break

        if not advanced:
            try:
                alive = bool(getattr(win, "_proc").is_alive())
            except Exception:
                alive = False
            try:
                last_status = str(getattr(win, "_last_backend_status_text", ""))
            except Exception:
                last_status = ""

            print("FAILED: playhead did not advance after TransportPlay")
            print(f"  backend_alive={alive}")
            if last_status:
                print(f"  last_status={last_status}")
            win.close()
            app.exit(5)
            return

        # Scroll the waveform safely.
        try:
            bar = win.scroll_area.horizontalScrollBar()
            if bar.maximum() <= 0:
                # Force some range.
                bar.setRange(0, 2000)
            bar.setValue(int(bar.maximum() * 0.5))
            QTest.qWait(50)
            bar.setValue(int(bar.maximum() * 0.9))
            QTest.qWait(50)
        except Exception as e:
            print(f"FAILED: scroll interaction crashed: {e}")
            win.close()
            app.exit(2)
            return

        # Move the waveform slider (seek).
        try:
            if win.waveform_slider.maximum() > 0:
                win.waveform_slider.setValue(int(win.waveform_slider.maximum() * 0.25))
            QTest.qWait(100)
        except Exception as e:
            print(f"FAILED: waveform slider crashed: {e}")
            win.close()
            app.exit(3)
            return

        # Jog dial bursts in both directions.
        try:
            for v in (10, 20, 30, 40, 35, 25, 15, 5, 0, 5, 10):
                win.jog_dial.setValue(v)
                QTest.qWait(15)
        except Exception as e:
            print(f"FAILED: jog dial crashed: {e}")
            win.close()
            app.exit(4)
            return

        QTest.qWait(300)
        try:
            end_frame["v"] = int(getattr(win.waveform, "position_frame", 0))
        except Exception:
            end_frame["v"] = 0

        # Stop and close.
        try:
            from engine.editor_audio_service import TransportStop

            win._send(TransportStop())
        except Exception:
            pass

        win.close()

        # Verify playhead moved at least a little.
        sf = start_frame["v"] or 0
        ef = end_frame["v"] or 0
        if ef <= sf:
            print(f"FAILED: playhead did not advance (start={sf}, end={ef})")
            app.exit(6)
            return

        print(f"OK: jog+scroll smoke passed (start={sf}, end={ef})")
        app.exit(0)

    QTimer.singleShot(200, run_steps)
    print("[jog_scroll] entering app.exec()", file=sys.stderr, flush=True)
    rc = int(app.exec())
    print(f"[jog_scroll] app.exec() returned rc={rc}", file=sys.stderr, flush=True)
    try:
        if hang_f is not None:
            try:
                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass
            try:
                hang_f.flush()
                hang_f.close()
            except Exception:
                pass
    except Exception:
        pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
