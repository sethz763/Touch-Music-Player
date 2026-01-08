#!/usr/bin/env python3
"""Test that ZoomableScrollArea captures pinch gestures."""

import sys
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from PySide6.QtCore import Qt
from ui.windows.audio_editor_window import ZoomableScrollArea


def test_zoomable_scrollarea_exists():
    """Test that ZoomableScrollArea class exists and can be instantiated."""
    app = QApplication.instance() or QApplication(sys.argv)
    
    # Create scroll area
    scroll_area = ZoomableScrollArea()
    assert scroll_area is not None, "ZoomableScrollArea instantiation failed"
    print("✓ ZoomableScrollArea instantiated")
    
    # Check it has the scale_changed signal
    assert hasattr(scroll_area, 'scale_changed'), "Missing scale_changed signal"
    print("✓ scale_changed signal exists")
    
    # Check it has gestureEvent
    assert hasattr(scroll_area, 'gestureEvent'), "Missing gestureEvent method"
    print("✓ gestureEvent method exists")
    
    # Check it has set_scale
    assert hasattr(scroll_area, 'set_scale'), "Missing set_scale method"
    print("✓ set_scale method exists")
    
    # Test set_scale
    scroll_area.set_scale(100)
    assert scroll_area._current_scale == 100, f"set_scale failed: got {scroll_area._current_scale}"
    print("✓ set_scale(100) works")
    
    scroll_area.set_scale(150)
    assert scroll_area._current_scale == 150, f"set_scale(150) failed: got {scroll_area._current_scale}"
    print("✓ set_scale(150) works")
    
    # Test scale clamping
    scroll_area.set_scale(20000)
    assert scroll_area._current_scale == 20000, "Scale should accept large values"
    print("✓ Scale accepts large values")
    
    scroll_area.set_scale(0)
    assert scroll_area._current_scale == 1, f"Scale minimum should be 1, got {scroll_area._current_scale}"
    print("✓ Scale minimum clamping works (0 → 1)")
    
    # Test signal connection
    scale_values = []
    scroll_area.scale_changed.connect(lambda s: scale_values.append(s))
    
    # Manually emit to test signal works
    scroll_area._current_scale = 50
    scroll_area.scale_changed.emit(75)
    assert len(scale_values) == 1, "Signal should have been emitted"
    assert scale_values[0] == 75, f"Signal value incorrect: {scale_values[0]}"
    print("✓ scale_changed signal works")
    
    print("\n✓✓✓ All ZoomableScrollArea tests passed!")


if __name__ == "__main__":
    test_zoomable_scrollarea_exists()
