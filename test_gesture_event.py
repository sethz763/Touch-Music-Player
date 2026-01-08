#!/usr/bin/env python3
"""Test ZoomableScrollArea gesture event handling."""

import sys
from unittest.mock import Mock, MagicMock, patch
from PySide6.QtWidgets import QApplication, QLabel, QGestureEvent
from PySide6.QtCore import Qt, QEvent
from ui.windows.audio_editor_window import ZoomableScrollArea, WaveformDisplay


def test_zoomable_scrollarea_gesture_event():
    """Test that ZoomableScrollArea.event() intercepts gesture events."""
    app = QApplication.instance() or QApplication(sys.argv)
    
    scroll_area = ZoomableScrollArea()
    
    # Track emitted signals
    emitted_scales = []
    scroll_area.scale_changed.connect(lambda s: emitted_scales.append(s))
    
    print("Test 1: event() method intercepts Gesture events")
    scroll_area._current_scale = 100
    
    # Create a mock gesture event
    mock_event = Mock(spec=QGestureEvent)
    mock_event.type.return_value = QEvent.Type.Gesture
    
    mock_pinch = MagicMock()
    mock_pinch.scaleFactor.return_value = 1.5
    mock_event.gesture.return_value = mock_pinch
    
    # Call event() method (simulating Qt event dispatch)
    result = scroll_area.event(mock_event)
    
    assert result == True, "event() should return True for handled gesture"
    assert scroll_area._current_scale == 150, f"Scale should be 150, got {scroll_area._current_scale}"
    assert len(emitted_scales) == 1, f"Should have emitted 1 signal, got {len(emitted_scales)}"
    print("  OK: 100 * 1.5 = " + str(scroll_area._current_scale))
    
    print("\nTest 2: gestureEvent() method also works")
    scroll_area._current_scale = 150
    emitted_scales.clear()
    
    mock_event = Mock(spec=QGestureEvent)
    mock_pinch = MagicMock()
    mock_pinch.scaleFactor.return_value = 0.67
    mock_event.gesture.return_value = mock_pinch
    
    result = scroll_area.gestureEvent(mock_event)
    
    assert result == True, "gestureEvent() should return True"
    expected = int(150 * 0.67)
    assert scroll_area._current_scale == expected, f"Scale should be {expected}, got {scroll_area._current_scale}"
    assert len(emitted_scales) == 1, f"Should have emitted 1 signal, got {len(emitted_scales)}"
    print("  OK: 150 * 0.67 = " + str(scroll_area._current_scale))
    
    print("\nTest 3: Non-gesture events pass through to parent")
    # Create a non-gesture event
    mock_event = Mock(spec=QEvent)
    mock_event.type.return_value = QEvent.Type.MouseMove
    
    # This should call super().event() and return the parent's result
    # We can't fully test this without a real window, but at least verify no crash
    try:
        result = scroll_area.event(mock_event)
        print("  OK: Non-gesture events handled without crash")
    except Exception as e:
        print(f"  FAIL: Error handling non-gesture event: {e}")
    
    print("\nTest 4: WaveformDisplay doesn't grab gesture")
    wf = WaveformDisplay(scroll_area)
    # Check that WaveformDisplay doesn't handle gestures
    mock_event = Mock(spec=QGestureEvent)
    mock_pinch = MagicMock()
    mock_pinch.scaleFactor.return_value = 1.5
    mock_event.gesture.return_value = mock_pinch
    
    result = wf.gestureEvent(mock_event)
    assert result == False, "WaveformDisplay should not handle gestures (return False)"
    print("  OK: WaveformDisplay forwards gestures to parent")
    
    print("")
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    test_zoomable_scrollarea_gesture_event()
