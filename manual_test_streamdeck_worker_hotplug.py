import multiprocessing as mp
import queue
import sys
import time


def main() -> int:
    seconds = 30.0
    try:
        if len(sys.argv) >= 2:
            seconds = float(sys.argv[1])
    except Exception:
        seconds = 30.0
    seconds = float(max(1.0, min(600.0, seconds)))

    ctx = mp.get_context("spawn")
    cmd_q = ctx.Queue()
    evt_q = ctx.Queue()
    stop_event = ctx.Event()

    # Import inside main for Windows spawn friendliness.
    from gui.streamdeck_worker import run_streamdeck_worker

    proc = ctx.Process(
        target=run_streamdeck_worker,
        args=(cmd_q, evt_q, stop_event),
        daemon=False,
        name="streamdeck-worker-harness",
    )
    proc.start()

    print("[harness] worker pid=", proc.pid)
    print("[harness] If you have a Stream Deck XL:")
    print("          1) Start with it unplugged (optional)")
    print("          2) Plug it in now; you should see a connected event")
    print("          3) Press a key; you should see key events")
    print("          4) Unplug/replug; you should see connected False/True")
    print(f"[harness] Running for ~{seconds:.0f}s...")

    # Send a few no-op render commands to exercise queue plumbing.
    try:
        for k in (0, 24, 30):
            cmd_q.put_nowait(
                {
                    "type": "render",
                    "key": int(k),
                    "text": f"K{k}",
                    "active_level": 0.0,
                    "corner_text": "H",
                }
            )
    except Exception:
        pass

    deadline = time.time() + seconds
    saw_any_evt = False

    try:
        while time.time() < deadline:
            # If the worker died, surface that immediately.
            if not proc.is_alive():
                code = proc.exitcode
                print(f"[harness] worker exited early exitcode={code}")
                return 2

            try:
                evt = evt_q.get(timeout=0.25)
            except queue.Empty:
                continue

            saw_any_evt = True
            print("[evt]", evt)
    finally:
        try:
            cmd_q.put_nowait({"type": "shutdown"})
        except Exception:
            pass
        try:
            stop_event.set()
        except Exception:
            pass
        try:
            proc.join(timeout=3.0)
        except Exception:
            pass
        if proc.is_alive():
            try:
                proc.terminate()
            except Exception:
                pass

    if not saw_any_evt:
        print("[harness] No events received (this is normal if no deck was ever connected).")

    print("[harness] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
