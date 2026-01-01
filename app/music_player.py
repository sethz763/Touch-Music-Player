from __future__ import annotations

def main() -> int:
    import multiprocessing as mp
    # Set spawn method BEFORE importing anything that uses multiprocessing
    # This must be done in the main thread before any subprocess creation
    mp.set_start_method("spawn", force=True)
    
    from PySide6.QtWidgets import QApplication
    from ui.windows.main_window import MainWindow
    
    app = QApplication([])
    w = MainWindow()
    w.resize(900, 600)
    w.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
