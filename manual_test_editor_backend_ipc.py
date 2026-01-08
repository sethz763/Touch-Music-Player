from __future__ import annotations

import time


def main() -> int:
    from engine.editor_audio_service import (
        LoadFile,
        Shutdown,
        Status,
        TransportPlay,
        start_editor_audio_backend,
    )

    proc, cmd_conn, evt_conn = start_editor_audio_backend()

    # Drain initial status
    t0 = time.time()
    while time.time() - t0 < 1.0:
        try:
            if not evt_conn.poll(0):
                raise RuntimeError("empty")
            evt = evt_conn.recv()
            if isinstance(evt, Status):
                print("status:", evt.text)
        except Exception:
            time.sleep(0.02)

    # Send a play command (no file needed to see status)
    cmd_conn.send(TransportPlay())

    got = False
    t1 = time.time()
    while time.time() - t1 < 2.0:
        try:
            if not evt_conn.poll(0):
                raise RuntimeError("empty")
            evt = evt_conn.recv()
            if isinstance(evt, Status):
                print("status:", evt.text)
                if "TransportPlay" in str(evt.text):
                    got = True
                    break
        except Exception:
            time.sleep(0.02)

    cmd_conn.send(Shutdown())
    try:
        proc.join(timeout=1.0)
    except Exception:
        pass

    if not got:
        print("FAILED: did not receive TransportPlay status")
        return 2

    print("OK: backend IPC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
