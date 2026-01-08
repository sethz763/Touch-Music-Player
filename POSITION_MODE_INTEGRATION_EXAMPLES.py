#!/usr/bin/env python3
"""
Example integration of position mode configuration into MainWindow.

This shows how to optionally integrate the new position mode setting
into your main application if you want users to be able to switch modes.
"""

# =============================================================================
# OPTION 1: Simple programmatic toggle (no UI)
# =============================================================================

def example_programmatic_toggle():
    """Set position mode programmatically during app startup."""
    from gui.engine_adapter import EngineAdapter
    
    # Create your adapter normally
    adapter = EngineAdapter(cmd_q, evt_q, parent=main_window)
    
    # Optionally switch modes based on config or environment variable
    use_absolute_position = os.environ.get('STEPD_ABSOLUTE_POSITION', '0') == '1'
    adapter.set_engine_position_relative_to_trim_markers(not use_absolute_position)
    
    print(f"Position mode: {'absolute' if use_absolute_position else 'trimmed'}")


# =============================================================================
# OPTION 2: Settings dialog integration (with UI control)
# =============================================================================

# Add this to your settings dialog/window:

def add_position_mode_setting_to_dialog():
    """
    Add a position mode toggle to your settings dialog.
    
    In your settings dialog class (e.g., SettingsWindow):
    """
    from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QGroupBox
    
    # Create checkbox widget
    position_mode_checkbox = QCheckBox("Use Absolute File Position Mode")
    position_mode_checkbox.setToolTip(
        "When enabled, elapsed time shows absolute file position.\n"
        "When disabled (default), elapsed time is relative to in/out markers."
    )
    
    # Create group box for organization
    timing_group = QGroupBox("Time Display Options")
    layout = QVBoxLayout()
    layout.addWidget(position_mode_checkbox)
    timing_group.setLayout(layout)
    
    # Connect to handler
    position_mode_checkbox.stateChanged.connect(on_position_mode_changed)
    
    # Load current setting
    position_mode_checkbox.setChecked(load_position_mode_from_settings())
    
    return timing_group


def on_position_mode_changed(state):
    """Handle position mode checkbox change."""
    # Convert Qt checkbox state to boolean
    use_absolute = bool(state)
    
    # Update engine adapter
    main_window.engine_adapter.set_engine_position_relative_to_trim_markers(not use_absolute)
    
    # Save to settings
    save_position_mode_to_settings(use_absolute)


def load_position_mode_from_settings():
    """Load position mode setting from config file."""
    # Implement based on your settings storage method
    # Default to trimmed mode (False = not absolute = trimmed)
    return False


def save_position_mode_to_settings(use_absolute: bool):
    """Save position mode setting to config file."""
    # Implement based on your settings storage method
    pass


# =============================================================================
# OPTION 3: Context menu for quick toggle (advanced users)
# =============================================================================

def add_position_mode_to_context_menu():
    """
    Add position mode toggle to right-click context menu.
    
    In your MainWindow or similar:
    """
    # Create menu action
    toggle_action = QAction("Toggle Position Mode (Absolute/Trimmed)")
    toggle_action.triggered.connect(toggle_position_mode)
    
    # Add to context menu
    context_menu.addSeparator()
    context_menu.addAction(toggle_action)


def toggle_position_mode():
    """Toggle between trimmed and absolute position modes."""
    current_mode = get_current_position_mode()
    new_mode = not current_mode
    
    adapter.set_engine_position_relative_to_trim_markers(new_mode)
    
    # Show feedback to user
    mode_name = "Trimmed" if new_mode else "Absolute"
    main_window.statusBar().showMessage(f"Position Mode: {mode_name}", 2000)
    
    # Save preference
    save_position_mode_to_settings(not new_mode)


def get_current_position_mode():
    """Get current position mode setting."""
    return getattr(adapter, '_position_relative_to_trim_markers', True)


# =============================================================================
# OPTION 4: Automatic mode selection based on content
# =============================================================================

def select_position_mode_based_on_context(file_path: str):
    """
    Intelligently choose position mode based on file or operation.
    
    For example:
    - Use absolute mode when editing (detailed file position)
    - Use trimmed mode when playing (natural user experience)
    """
    if file_path.endswith('.mp3'):
        # Streaming formats might benefit from absolute positioning
        adapter.set_engine_position_relative_to_trim_markers(False)
    else:
        # Standard playback: use trimmed (default)
        adapter.set_engine_position_relative_to_trim_markers(True)


# =============================================================================
# DIAGNOSTIC HELPER: Verify modes are working correctly
# =============================================================================

def verify_position_modes_in_test():
    """
    Test both position modes during startup to verify they're working.
    
    Useful for diagnostics or testing.
    """
    import time
    
    # Create a test cue
    cue_id = "mode-test"
    in_frame = 24000
    out_frame = 72000
    sr = 48000
    
    adapter._cue_in_frames[cue_id] = in_frame
    adapter._cue_out_frames[cue_id] = out_frame
    adapter._cue_sample_rates[cue_id] = sr
    adapter._cue_total_seconds[cue_id] = 2.0
    
    # Test Mode 1: Trimmed
    adapter.set_engine_position_relative_to_trim_markers(True)
    remaining, total = adapter._calculate_trimmed_time(cue_id, 0.5, 2.0)
    assert abs(remaining - 0.5) < 0.001, f"Trimmed mode failed: {remaining}"
    
    # Test Mode 2: Absolute
    adapter.set_engine_position_relative_to_trim_markers(False)
    remaining, total = adapter._calculate_trimmed_time(cue_id, 1.0, 2.0)
    assert abs(remaining - 0.5) < 0.001, f"Absolute mode failed: {remaining}"
    
    print("✓ Both position modes verified working correctly")


# =============================================================================
# ENVIRONMENT VARIABLE APPROACH (No UI, for automation)
# =============================================================================

def init_position_mode_from_environment():
    """
    Initialize position mode from environment variable.
    
    Usage:
        STEPD_POSITION_MODE=absolute python -m app.music_player
        STEPD_POSITION_MODE=trimmed python -m app.music_player
    """
    import os
    
    mode_env = os.environ.get('STEPD_POSITION_MODE', 'trimmed').lower()
    
    if mode_env == 'absolute':
        adapter.set_engine_position_relative_to_trim_markers(False)
        print("Position mode: ABSOLUTE (from STEPD_POSITION_MODE env var)")
    else:
        adapter.set_engine_position_relative_to_trim_markers(True)
        print("Position mode: TRIMMED (default)")


if __name__ == "__main__":
    print("""
    Position Mode Integration Examples
    ===================================
    
    Choose one of the following approaches:
    
    1. Programmatic toggle (no UI):
       Call: example_programmatic_toggle()
       Use case: Automated testing, specific workflows
       
    2. Settings dialog (full UI):
       Call: add_position_mode_setting_to_dialog()
       Use case: User preferences
       
    3. Context menu (quick access):
       Call: add_position_mode_to_context_menu()
       Use case: Power users, debugging
       
    4. Automatic based on context:
       Call: select_position_mode_based_on_context()
       Use case: Different modes for different operations
       
    5. Environment variable:
       Set: STEPD_POSITION_MODE=absolute or trimmed
       Use case: CI/CD, deployment configuration
       
    6. Verification test:
       Call: verify_position_modes_in_test()
       Use case: Startup diagnostics
    """)
