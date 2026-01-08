#!/usr/bin/env python3
"""Debug pinch gesture behavior."""

import sys
import os
os.environ['STEPD_PINCH_DEBUG'] = '1'

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer
from ui.windows.audio_editor_window import ZoomableScrollArea, WaveformDisplay


def simulate_pinches():
    """Create a simple test window and simulate pinches."""
    app = QApplication.instance() or QApplication(sys.argv)
    
    # Create window with ZoomableScrollArea
    window = QWidget()
    window.setWindowTitle("Pinch Test")
    layout = QVBoxLayout(window)
    
    scroll_area = ZoomableScrollArea()
    scroll_area._current_scale = 100
    
    wf = WaveformDisplay(scroll_area)
    wf.set_audio(
        __import__('numpy').zeros((2, 48000), dtype=__import__('numpy').float32),
        duration_frames=48000,
        sample_rate=48000,
        channels=2,
    )
    scroll_area.setWidget(wf)
    
    label = QLabel()
    label.setMinimumHeight(50)
    
    def on_scale_changed(scale):
        label.setText(f"Scale: {scale}")
        print(f"[TEST] Scale changed to {scale}", flush=True)
    
    scroll_area.scale_changed.connect(on_scale_changed)
    
    layout.addWidget(scroll_area)
    layout.addWidget(label)
    
    window.setMinimumSize(600, 400)
    
    print("[TEST] Window created, initial scale = 100", flush=True)
    
    # Simulate pinch events
    def do_pinches():
        from unittest.mock import Mock, MagicMock
        from PySide6.QtWidgets import QGestureEvent
        
        print("\n[TEST] Simulating pinch out (1.5x) to go from 100 to 150", flush=True)
        mock_event = Mock(spec=QGestureEvent)
        mock_event.type.return_value = scroll_area.eventType() if hasattr(scroll_area, 'eventType') else 77  # Gesture
        mock_pinch = MagicMock()
        mock_pinch.scaleFactor.return_value = 1.5
        mock_event.gesture.return_value = mock_pinch
        
        result = scroll_area.event(mock_event)
        print(f"[TEST] Pinch out result: {result}, scale now = {scroll_area._current_scale}", flush=True)
        
        QTimer.singleShot(500, lambda: do_pinch_in())
    
    def do_pinch_in():
        from unittest.mock import Mock, MagicMock
        from PySide6.QtWidgets import QGestureEvent
        
        print("\n[TEST] Simulating pinch in (0.67x) to go from 150 to ~100", flush=True)
        mock_event = Mock(spec=QGestureEvent)
        mock_event.type.return_value = scroll_area.eventType() if hasattr(scroll_area, 'eventType') else 77
        mock_pinch = MagicMock()
        mock_pinch.scaleFactor.return_value = 0.67
        mock_event.gesture.return_value = mock_pinch
        
        result = scroll_area.event(mock_event)
        print(f"[TEST] Pinch in result: {result}, scale now = {scroll_area._current_scale}", flush=True)
        
        QTimer.singleShot(500, app.quit)
    
    QTimer.singleShot(100, do_pinches)
    
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(simulate_pinches())
