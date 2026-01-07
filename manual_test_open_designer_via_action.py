from __future__ import annotations


def main() -> int:
    import sys
    import traceback

    import multiprocessing as mp

    # Match app startup behavior (engine/process spawning relies on spawn on Windows).
    try:
        mp.set_start_method("spawn", force=True)
    except Exception:
        pass

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication([])

    def _try_open() -> None:
        try:
            from ui.windows.button_image_designer_window import ButtonImageDesignerWindow

            d = ButtonImageDesignerWindow(parent=None)
            d.show()
            d.raise_()
            d.activateWindow()
            print("DESIGNER_OPEN_OK")
        except Exception:
            print("DESIGNER_OPEN_FAILED")
            traceback.print_exc()

    QTimer.singleShot(0, _try_open)
    QTimer.singleShot(900, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
