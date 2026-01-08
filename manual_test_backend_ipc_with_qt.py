from __future__ import annotations

import time


def main() -> int:
    try:
        import multiprocessing as mp

        mp.freeze_support()
        mp.set_start_method("spawn", force=True)
    except Exception:
        pass

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from engine.editor_audio_service import Shutdown, Status, TransportPlay, start_editor_audio_backend

    proc, cmd_conn, evt_conn = start_editor_audio_backend()

    app = QApplication([])

    got_play = {"v": False}

    def _send_cmd(conn: object, msg: object) -> None:
        """Compatibility shim: old harnesses used mp.Queue; new backend uses Pipe."""

        try:
            send = getattr(conn, "send", None)
            if callable(send):
                send(msg)
                return
        except Exception:
            pass
        try:
            put = getattr(conn, "put", None)
            if callable(put):
                put(msg)
        except Exception:
            pass

    def send_play() -> None:
        _send_cmd(cmd_conn, TransportPlay())

    def poll() -> None:
        # Drain any statuses
        while True:
            try:
                if not evt_conn.poll(0):
                    break
                evt = evt_conn.recv()
            except Exception:
                break
            if isinstance(evt, Status):
                print("status:", evt.text)
                if "TransportPlay" in str(evt.text):
                    got_play["v"] = True
            else:
                # Helpful if the backend sends unexpected event types.
                try:
                    print("event:", type(evt).__name__, evt)
                except Exception:
                    print("event:", type(evt).__name__)

        if got_play["v"]:
            try:
                _send_cmd(cmd_conn, Shutdown())
            except Exception:
                pass
            try:
                proc.join(timeout=1.0)
            except Exception:
                pass
            app.exit(0)

    QTimer.singleShot(200, send_play)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(50)

    # Safety timeout
    def timeout() -> None:
        print("FAILED: no TransportPlay status")
        try:
            _send_cmd(cmd_conn, Shutdown())
        except Exception:
            pass
        app.exit(2)

    QTimer.singleShot(2000, timeout)

    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
