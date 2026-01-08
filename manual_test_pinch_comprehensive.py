#!/usr/bin/env python3
"""Comprehensive test of pinch-to-zoom scale manipulation."""
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPinchGesture, QGestureEvent

sys.path.insert(0, str(Path(__file__).parent))
from ui.windows.audio_editor_window import AudioEditorWindow

def main():
    app = QApplication(sys.argv)
    
    editor = AudioEditorWindow(
        file_path="test_audio.wav",
        track_id="pinch-comprehensive-test",
    )
    editor.show()
    
    test_cases = [
        (2.0, "double pinch (zoom out far)"),
        (1.2, "small pinch apart (zoom out a bit)"),
        (1.0, "no pinch"),
        (0.8, "small pinch together (zoom in a bit)"),
        (0.5, "half pinch (zoom in far)"),
    ]
    
    def run_tests():
        initial_scale = editor.waveform.scale
        print(f"[test] starting scale: {initial_scale}")
        
        for factor, description in test_cases:
            if factor == 1.0:
                print(f"[test] skipping no-op test: {description}")
                continue
            
            gesture = QPinchGesture()
            gesture.setScaleFactor(factor)
            event = QGestureEvent([gesture])
            
            before = editor.waveform.scale
            result = editor.waveform.gestureEvent(event)
            after = editor.waveform.scale
            
            expected_after = int(before * factor)
            expected_after = max(1, min(10000, expected_after))
            
            status = "✓" if after == expected_after else "✗"
            print(f"[test] {status} {description}: {before} * {factor} -> {after} (expected {expected_after})")
        
        print("[test] comprehensive pinch test completed")
        QApplication.quit()
    
    QTimer.singleShot(1500, run_tests)
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
