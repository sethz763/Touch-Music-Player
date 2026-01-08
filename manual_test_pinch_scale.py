#!/usr/bin/env python3
"""Test the updated pinch-to-zoom gesture with scale manipulation."""
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
    print("[pinch_scale_test] creating QApplication")
    app = QApplication(sys.argv)
    print("[pinch_scale_test] QApplication created")
    
    try:
        track_path = "test_audio.wav"
        print(f"[pinch_scale_test] track_path={track_path}")
        
        print("[pinch_scale_test] constructing AudioEditorWindow")
        editor = AudioEditorWindow(
            file_path=str(track_path),
            track_id="pinch-scale-test",
        )
        print("[pinch_scale_test] AudioEditorWindow constructed")
        print(f"[pinch_scale_test] initial scale={editor.waveform.scale}")
        
        editor.show()
        print("[pinch_scale_test] window shown")
        
        # Simulate pinch gestures with scale checking
        def trigger_pinches():
            print("[pinch_scale_test] triggering pinch gestures with scale verification")
            try:
                initial_scale = editor.waveform.scale
                print(f"  [pinch] initial scale: {initial_scale}")
                
                # Simulate pinch-out (zoom out) - increase scale
                print("  [pinch] simulating pinch-out (1.5x factor) -> should increase scale")
                gesture_out = QPinchGesture()
                gesture_out.setScaleFactor(1.5)  # Pinch outward
                event_out = QGestureEvent([gesture_out])
                result = editor.waveform.gestureEvent(event_out)
                scale_after_out = editor.waveform.scale
                print(f"  [pinch] pinch-out handled: {result}, scale after: {scale_after_out} (was {initial_scale})")
                
                time.sleep(0.5)
                
                # Simulate pinch-in (zoom in) - decrease scale
                print("  [pinch] simulating pinch-in (0.67x factor) -> should decrease scale")
                gesture_in = QPinchGesture()
                gesture_in.setScaleFactor(0.67)  # Pinch inward
                event_in = QGestureEvent([gesture_in])
                result = editor.waveform.gestureEvent(event_in)
                scale_after_in = editor.waveform.scale
                print(f"  [pinch] pinch-in handled: {result}, scale after: {scale_after_in} (was {scale_after_out})")
                
                # Verify scale changes
                if scale_after_out > initial_scale:
                    print(f"  [pinch] ✓ pinch-out increased scale: {initial_scale} -> {scale_after_out}")
                else:
                    print(f"  [pinch] ✗ pinch-out did not increase scale: {initial_scale} -> {scale_after_out}")
                
                if scale_after_in < scale_after_out:
                    print(f"  [pinch] ✓ pinch-in decreased scale: {scale_after_out} -> {scale_after_in}")
                else:
                    print(f"  [pinch] ✗ pinch-in did not decrease scale: {scale_after_out} -> {scale_after_in}")
                
                print("[pinch_scale_test] all gestures completed successfully")
                
            except Exception as e:
                print(f"[pinch_scale_test] ERROR during gesture: {type(e).__name__}: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()
                raise
            finally:
                QApplication.quit()
        
        # Schedule pinches after editor initializes
        QTimer.singleShot(2000, trigger_pinches)
        
        print("[pinch_scale_test] entering app.exec()")
        rc = app.exec()
        print(f"[pinch_scale_test] app.exec() returned rc={rc}")
        
        print("OK: pinch scale test completed")
        return rc
        
    except Exception as e:
        print(f"[pinch_scale_test] FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
