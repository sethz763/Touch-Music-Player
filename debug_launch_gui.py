from __future__ import annotations

import multiprocessing as mp
import sys
import traceback


def _excepthook(exc_type, exc, tb):
    traceback.print_exception(exc_type, exc, tb)
    sys.stderr.flush()


sys.excepthook = _excepthook


def main() -> int:
    mp.set_start_method("spawn", force=True)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    print("[debug_launch_gui] creating QApplication")
    app = QApplication([])

    print("[debug_launch_gui] importing MainWindow")
    from ui.windows.main_window import MainWindow

    try:
        print("[debug_launch_gui] constructing MainWindow")
        w = MainWindow()
    except Exception:
        print("[debug_launch_gui] MainWindow() raised:")
        traceback.print_exc()
        return 1

    try:
        print("[debug_launch_gui] showing MainWindow")
        w.resize(900, 600)
        w.show()
    except Exception:
        print("[debug_launch_gui] w.show() raised:")
        traceback.print_exc()
        return 1

    QTimer.singleShot(1500, app.quit)
    rc = app.exec()
    print(f"[debug_launch_gui] app.exec() returned {rc}")
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
