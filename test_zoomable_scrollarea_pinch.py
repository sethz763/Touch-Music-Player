#!/usr/bin/env python3
"""Test ZoomableScrollArea pinch gesture handling."""

import sys
from unittest.mock import Mock, MagicMock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QGestureEvent
from ui.windows.audio_editor_window import ZoomableScrollArea


def test_pinch_gesture_scale_changes():
    """Test that simulated pinch gestures change scale correctly."""
    app = QApplication.instance() or QApplication(sys.argv)
    
    scroll_area = ZoomableScrollArea()
    
    # Track emitted signals
    emitted_scales = []
    scroll_area.scale_changed.connect(lambda s: emitted_scales.append(s))
    
    print("Test 1: Pinch out (1.5x scale factor)")
    scroll_area._current_scale = 100
    
    # Create a mock pinch gesture
    mock_event = Mock(spec=QGestureEvent)
    mock_pinch = MagicMock()
    mock_pinch.scaleFactor.return_value = 1.5  # Pinch apart
    
    mock_event.gesture.return_value = mock_pinch
    
    # Call gestureEvent
    result = scroll_area.gestureEvent(mock_event)
    
    assert result == True, "gestureEvent should return True"
    assert scroll_area._current_scale == 150, f"Scale should be 150, got {scroll_area._current_scale}"
    assert len(emitted_scales) == 1, f"Should have emitted 1 signal, got {len(emitted_scales)}"
    assert emitted_scales[-1] == 150, f"Emitted scale should be 150, got {emitted_scales[-1]}"
    print(f"  ✓ 100 * 1.5 = {scroll_area._current_scale}")
    
    print("\nTest 2: Pinch in (0.67x scale factor)")
    scroll_area._current_scale = 150
    
    mock_pinch.scaleFactor.return_value = 0.67  # Pinch together
    mock_event.gesture.return_value = mock_pinch
    
    result = scroll_area.gestureEvent(mock_event)
    
    assert result == True, "gestureEvent should return True"
    expected = int(150 * 0.67)
    assert scroll_area._current_scale == expected, f"Scale should be {expected}, got {scroll_area._current_scale}"
    assert len(emitted_scales) == 2, f"Should have emitted 2 signals, got {len(emitted_scales)}"
    print(f"  ✓ 150 * 0.67 = {scroll_area._current_scale}")
    
    print("\nTest 3: Rapid pinch sequence")
    scroll_area._current_scale = 100
    emitted_scales.clear()
    
    for i, factor in enumerate([1.2, 1.3, 0.8, 0.9, 1.1]):
        mock_pinch.scaleFactor.return_value = factor
        mock_event.gesture.return_value = mock_pinch
        result = scroll_area.gestureEvent(mock_event)
        assert result == True, f"gestureEvent {i} should return True"
        print(f"  ✓ Pinch {i+1}: factor={factor}, scale={scroll_area._current_scale}")
    
    assert len(emitted_scales) == 5, f"Should have emitted 5 signals, got {len(emitted_scales)}"
    print(f"  ✓ Rapid pinch sequence complete: {emitted_scales}")
    
    print("\nTest 4: Scale clamping (min)")
    scroll_area._current_scale = 2
    mock_pinch.scaleFactor.return_value = 0.1  # Very small, should clamp to 1
    mock_event.gesture.return_value = mock_pinch
    
    result = scroll_area.gestureEvent(mock_event)
    assert scroll_area._current_scale >= 1, f"Scale should be clamped to min 1, got {scroll_area._current_scale}"
    print(f"  ✓ 2 * 0.1 clamped to {scroll_area._current_scale}")
    
    print("\nTest 5: Scale clamping (max)")
    scroll_area._current_scale = 9000
    mock_pinch.scaleFactor.return_value = 1.5
    mock_event.gesture.return_value = mock_pinch
    
    result = scroll_area.gestureEvent(mock_event)
    assert scroll_area._current_scale <= 10000, f"Scale should be clamped to max 10000, got {scroll_area._current_scale}"
    print(f"  ✓ 9000 * 1.5 clamped to {scroll_area._current_scale}")
    
    print("\nTest 6: None event handling")
    result = scroll_area.gestureEvent(None)
    assert result == False, "gestureEvent(None) should return False"
    print("  ✓ None event returns False")
    
    print("\nTest 7: No pinch gesture in event")
    mock_event = Mock(spec=QGestureEvent)
    mock_event.gesture.return_value = None
    
    result = scroll_area.gestureEvent(mock_event)
    assert result == False, "gestureEvent with no pinch should return False"
    print("  ✓ No pinch gesture returns False")
    
    print("\nTest 8: Invalid scale factor")
    mock_event = Mock(spec=QGestureEvent)
    mock_pinch = MagicMock()
    mock_pinch.scaleFactor.return_value = 0  # Invalid, must be > 0
    
    mock_event.gesture.return_value = mock_pinch
    result = scroll_area.gestureEvent(mock_event)
    assert result == False, "gestureEvent with invalid scale_factor should return False"
    print("  ✓ Invalid scale factor (0) returns False")
    
    print("\n✓✓✓ All pinch gesture tests passed!")


if __name__ == "__main__":
    test_pinch_gesture_scale_changes()
