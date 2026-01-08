#!/usr/bin/env python3
"""Manual test for pinch-to-zoom gesture crash."""
import sys
import time
from pathlib import Path

from PySide6 import QtCore, QtGui
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtWidgets import QApplication, QGestureEvent, QPinchGesture

# Add repo to path
sys.path.insert(0, str(Path(__file__).parent))

from ui.windows.audio_editor_window import AudioEditorWindow

def main():
    print("[pinch_gesture] creating QApplication")
    app = QApplication(sys.argv)
    print("[pinch_gesture] QApplication created")
    
    try:
        track_path = "test_audio.wav"
        print(f"[pinch_gesture] track_path={track_path}")
        
        print("[pinch_gesture] constructing AudioEditorWindow")
        editor = AudioEditorWindow(
            file_path=str(track_path),
            track_id="pinch-gesture-test",
        )
        print("[pinch_gesture] AudioEditorWindow constructed")
        
        editor.show()
        print("[pinch_gesture] window shown")
        
        # Simulate pinch gestures after a delay
        def trigger_pinches():
            print("[pinch_gesture] triggering pinch gestures")
            try:
                # Simulate pinch-in (zoom)
                print("  [pinch] simulating pinch-in (zoom)")
                gesture_in = QPinchGesture()
                gesture_in.setScaleFactor(0.9)  # Pinch inward
                event_in = QGestureEvent([gesture_in])
                result = editor.waveform.gestureEvent(event_in)
                print(f"  [pinch] pinch-in handled: {result}")
                
                time.sleep(0.5)
                
                # Simulate pinch-out (unzoom)
                print("  [pinch] simulating pinch-out (unzoom)")
                gesture_out = QPinchGesture()
                gesture_out.setScaleFactor(1.1)  # Pinch outward
                event_out = QGestureEvent([gesture_out])
                result = editor.waveform.gestureEvent(event_out)
                print(f"  [pinch] pinch-out handled: {result}")
                
                print("[pinch_gesture] all gestures completed successfully")
                
            except Exception as e:
                print(f"[pinch_gesture] ERROR during gesture: {type(e).__name__}: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()
                raise
            finally:
                QApplication.quit()
        
        # Schedule pinches after editor initializes
        QTimer.singleShot(2000, trigger_pinches)
        
        print("[pinch_gesture] entering app.exec()")
        rc = app.exec()
        print(f"[pinch_gesture] app.exec() returned rc={rc}")
        
        print("OK: pinch gesture test completed")
        return rc
        
    except Exception as e:
        print(f"[pinch_gesture] FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
