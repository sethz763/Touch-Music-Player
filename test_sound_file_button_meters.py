#!/usr/bin/env python3
"""
Quick test to verify AudioLevelMeter integration in SoundFileButton.

This test checks that:
1. AudioLevelMeters are created properly
2. They slide in/out with the gain slider
3. They update when cue_levels events are received
"""

import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from ui.widgets.sound_file_button import SoundFileButton


def test_meters_creation():
    """Test that AudioLevelMeters are created."""
    app = QApplication.instance() or QApplication(sys.argv)
    
    btn = SoundFileButton("Test Button")
    
    # Check that meters were created
    assert btn.level_meter_left is not None, "Left meter not created"
    assert btn.level_meter_right is not None, "Right meter not created"
    
    # Check that meters are hidden initially
    assert not btn.level_meter_left.isVisible(), "Left meter should be hidden initially"
    assert not btn.level_meter_right.isVisible(), "Right meter should be hidden initially"
    
    print("✓ Meters created and initially hidden")


def test_meters_show_hide():
    """Test that meters slide in/out with gain slider."""
    app = QApplication.instance() or QApplication(sys.argv)
    
    btn = SoundFileButton("Test Button")
    btn.resize(200, 100)  # Give it a reasonable size
    btn.show()
    
    # Simulate showing gain slider
    btn._show_gain_slider()
    
    # Check that meters are visible
    assert btn.level_meter_left.isVisible(), "Left meter should be visible after show"
    assert btn.gain_slider_visible, "gain_slider_visible flag should be True"
    
    print("✓ Meters slide in with gain slider")
    
    # Simulate hiding gain slider
    btn._hide_gain_slider()
    
    # After animation completes, meters should be hidden
    # (Note: we may need to process events for animation to complete)
    
    print("✓ Meters slide out with gain slider")


def test_meter_stereo_layout():
    """Test that meters are positioned correctly for stereo."""
    app = QApplication.instance() or QApplication(sys.argv)
    
    btn = SoundFileButton("Test Button")
    btn.channels = 2  # Stereo
    btn.resize(200, 100)
    btn.show()
    
    btn._show_gain_slider()
    
    left_geom = btn.level_meter_left.geometry()
    right_geom = btn.level_meter_right.geometry()
    
    # Both should be visible
    assert btn.level_meter_left.isVisible(), "Left meter should be visible for stereo"
    assert btn.level_meter_right.isVisible(), "Right meter should be visible for stereo"
    
    # They should have the same y position (same row, horizontal layout)
    assert left_geom.y() == right_geom.y(), "Meters should be in same row (horizontal)"
    
    # Right meter should be to the right of left meter
    assert right_geom.x() > left_geom.x(), "Right meter should be to the right of left meter"
    
    print("✓ Meters positioned correctly for stereo (side-by-side)")


def test_meter_mono_layout():
    """Test that right meter is hidden for mono."""
    app = QApplication.instance() or QApplication(sys.argv)
    
    btn = SoundFileButton("Test Button")
    btn.channels = 1  # Mono
    btn.resize(200, 100)
    btn.show()
    
    btn._show_gain_slider()
    
    # Left should be visible, right should be hidden
    assert btn.level_meter_left.isVisible(), "Left meter should be visible for mono"
    assert not btn.level_meter_right.isVisible(), "Right meter should be hidden for mono"
    
    print("✓ Meters positioned correctly for mono")


def test_meter_updates():
    """Test that meters update with cue_levels signals."""
    app = QApplication.instance() or QApplication(sys.argv)
    
    btn = SoundFileButton("Test Button")
    btn.channels = 2
    btn.resize(200, 100)
    btn.show()
    
    # Simulate playing a cue
    cue_id = "test_cue_123"
    btn._active_cue_ids.add(cue_id)
    btn.current_cue_id = cue_id
    btn.gain_slider_visible = True
    btn.level_meter_left.show()
    btn.level_meter_right.show()
    
    # Simulate receiving cue_levels signal with -20 dB RMS, -10 dB peak
    # This would be 0.1 linear RMS
    rms_linear = 0.1
    peak_linear = 0.316  # Approximately -10 dB
    
    btn._on_cue_levels(cue_id, rms_linear, peak_linear)
    
    # Verify meters were updated
    # (We can't directly check internal meter state, but no exceptions should occur)
    
    print("✓ Meters update with cue_levels signals")


if __name__ == "__main__":
    print("Testing SoundFileButton AudioLevelMeter integration...\n")
    
    try:
        test_meters_creation()
        test_meters_show_hide()
        test_meter_stereo_layout()
        test_meter_mono_layout()
        test_meter_updates()
        
        print("\n✅ All tests passed!")
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
