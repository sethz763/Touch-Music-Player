from __future__ import annotations

def main() -> int:
    import sys
    import traceback
    from pathlib import Path
    import faulthandler

    import multiprocessing as mp
    # Set spawn method BEFORE importing anything that uses multiprocessing
    # This must be done in the main thread before any subprocess creation
    mp.set_start_method("spawn", force=True)
    
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer, Qt
    from ui.windows.main_window import MainWindow

    # If the GUI crashes during startup, write a traceback to disk so we don't
    # end up with a silent failure + orphaned audio subprocess printing logs.
    crash_path = Path("last_gui_crash.txt")
    hang_path = Path("last_gui_hang.txt")

    def _write_crash(exc: BaseException) -> None:
        try:
            crash_path.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        try:
            print("\n[GUI CRASH] See last_gui_crash.txt\n", file=sys.stderr)
            traceback.print_exc()
        except Exception:
            pass

    try:
        app = QApplication([])
        try:
            print("[GUI] QApplication created")
        except Exception:
            pass

        w = MainWindow()
        try:
            w.setGeometry(50, 50, 900, 600)
        except Exception:
            try:
                w.resize(900, 600)
                w.move(50, 50)
            except Exception:
                pass

        # Make absolutely sure we become visible (not minimized/off-screen) and foreground.
        try:
            w.showNormal()
        except Exception:
            pass
        w.show()

        # If the GUI thread deadlocks/freezes (white window), dump stack traces.
        # This is best-effort and only used for debugging in the field.
        try:
            hang_f = hang_path.open("w", encoding="utf-8")
            faulthandler.enable(file=hang_f)
            faulthandler.dump_traceback_later(15.0, repeat=False, file=hang_f)
        except Exception:
            hang_f = None

        def _bring_to_front() -> None:
            try:
                w.setWindowState((w.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
            except Exception:
                pass
            try:
                w.raise_()
                w.activateWindow()
            except Exception:
                pass

        try:
            QTimer.singleShot(150, _bring_to_front)
            QTimer.singleShot(750, _bring_to_front)
        except Exception:
            pass

        rc = app.exec()
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
    except Exception as e:
        _write_crash(e)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
