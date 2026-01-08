#!/usr/bin/env python3
"""
Position Mode Configuration - Integration Examples

This file shows practical examples of using the new position mode feature
in different scenarios.
"""

# ============================================================================
# Example 1: Default Usage (Trimmed Time)
# ============================================================================
"""
Use case: Standard playback with trimmed audio
Expected behavior: Time counts from 0 to duration
"""

def example_default_usage():
    from gui.engine_adapter import EngineAdapter
    import multiprocessing as mp
    
    cmd_q = mp.Queue()
    evt_q = mp.Queue()
    adapter = EngineAdapter(cmd_q, evt_q)
    
    # No need to set mode - trimmed time is default
    # Mode: Trimmed (elapsed starts at 0)
    
    # Play audio from 0.5s to 1.5s of file
    adapter.play_cue(
        file_path="/path/to/audio.wav",
        in_frame=24000,   # 0.5s @ 48kHz
        out_frame=72000,  # 1.5s @ 48kHz
    )
    
    # Users will see:
    # - Start: elapsed 0:00, remaining 1:00
    # - Mid: elapsed 0:30, remaining 0:30
    # - End: elapsed 1:00, remaining 0:00


# ============================================================================
# Example 2: Switching to Absolute Mode for Debugging
# ============================================================================
"""
Use case: Understanding engine behavior and file positions
Expected behavior: Time shows actual file position
"""

def example_debug_mode():
    from gui.engine_adapter import EngineAdapter
    import multiprocessing as mp
    
    cmd_q = mp.Queue()
    evt_q = mp.Queue()
    adapter = EngineAdapter(cmd_q, evt_q)
    
    # Enable absolute position mode for debugging
    adapter.set_engine_position_relative_to_trim_markers(False)
    # Mode: Absolute (elapsed shows file position)
    
    # Play same audio from 0.5s to 1.5s
    adapter.play_cue(
        file_path="/path/to/audio.wav",
        in_frame=24000,   # 0.5s @ 48kHz
        out_frame=72000,  # 1.5s @ 48kHz
    )
    
    # Developers will see:
    # - Start: elapsed 0:30 (file position 0.5s), remaining 1:00
    # - Mid: elapsed 1:00 (file position 1.0s), remaining 0:30
    # - End: elapsed 1:30 (file position 1.5s), remaining 0:00
    # 
    # This clearly shows the engine is working with file positions!


# ============================================================================
# Example 3: Conditional Mode Based on Application Type
# ============================================================================
"""
Use case: Different modes for different features
"""

def example_conditional_mode(is_debug_mode: bool = False):
    from gui.engine_adapter import EngineAdapter
    import multiprocessing as mp
    
    cmd_q = mp.Queue()
    evt_q = mp.Queue()
    adapter = EngineAdapter(cmd_q, evt_q)
    
    # Choose mode based on application context
    if is_debug_mode:
        adapter.set_engine_position_relative_to_trim_markers(False)
        print("Position mode: ABSOLUTE (debugging)")
    else:
        adapter.set_engine_position_relative_to_trim_markers(True)
        print("Position mode: TRIMMED (normal playback)")
    
    return adapter


# ============================================================================
# Example 4: Settings Integration
# ============================================================================
"""
Use case: User can switch mode from settings dialog
"""

def example_settings_integration():
    from gui.engine_adapter import EngineAdapter
    import multiprocessing as mp
    
    class AudioSettings:
        def __init__(self, adapter: EngineAdapter):
            self.adapter = adapter
            self.position_mode = True  # Default: trimmed
        
        def set_position_mode(self, trimmed: bool):
            """User selected new position mode from settings."""
            self.position_mode = trimmed
            self.adapter.set_engine_position_relative_to_trim_markers(trimmed)
            print(f"Position mode changed to: {'TRIMMED' if trimmed else 'ABSOLUTE'}")
        
        def get_position_mode(self) -> bool:
            """Get current position mode."""
            return self.position_mode
    
    # Usage
    cmd_q = mp.Queue()
    evt_q = mp.Queue()
    adapter = EngineAdapter(cmd_q, evt_q)
    settings = AudioSettings(adapter)
    
    # User toggles in settings UI
    settings.set_position_mode(False)  # Switch to absolute
    settings.set_position_mode(True)   # Switch back to trimmed


# ============================================================================
# Example 5: Testing Both Modes
# ============================================================================
"""
Use case: Unit test that verifies mode behavior
"""

def example_testing_modes():
    from gui.engine_adapter import EngineAdapter
    import multiprocessing as mp
    
    cmd_q = mp.Queue()
    evt_q = mp.Queue()
    adapter = EngineAdapter(cmd_q, evt_q)
    
    # Register a cue with trim points
    cue_id = "test-cue"
    in_frame = 24000
    out_frame = 72000
    sr = 48000
    total = 2.0
    
    adapter._cue_in_frames[cue_id] = in_frame
    adapter._cue_out_frames[cue_id] = out_frame
    adapter._cue_sample_rates[cue_id] = sr
    adapter._cue_total_seconds[cue_id] = total
    
    # Test Mode 1: Trimmed
    adapter.set_engine_position_relative_to_trim_markers(True)
    elapsed_trimmed, remaining_trimmed = adapter._calculate_trimmed_time(cue_id, 0.5, total)
    assert elapsed_trimmed == 0.5, f"Trimmed elapsed should be 0.5, got {elapsed_trimmed}"
    assert remaining_trimmed == 0.5, f"Trimmed remaining should be 0.5, got {remaining_trimmed}"
    print("✓ Mode 1 (trimmed) test passed")
    
    # Test Mode 2: Absolute
    adapter.set_engine_position_relative_to_trim_markers(False)
    elapsed_abs, remaining_abs = adapter._calculate_trimmed_time(cue_id, 1.0, total)
    assert elapsed_abs == 1.0, f"Absolute elapsed should be 1.0, got {elapsed_abs}"
    assert remaining_abs == 0.5, f"Absolute remaining should be 0.5, got {remaining_abs}"
    print("✓ Mode 2 (absolute) test passed")


# ============================================================================
# Main: Run Examples
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("Position Mode Configuration - Integration Examples")
    print("=" * 70)
    
    print("\nExample 1: Default Usage")
    print("-" * 70)
    # example_default_usage()  # Skipped (requires actual audio)
    print("Shows how to use default trimmed time mode")
    
    print("\nExample 2: Debug Mode")
    print("-" * 70)
    # example_debug_mode()  # Skipped (requires actual audio)
    print("Shows how to switch to absolute position mode")
    
    print("\nExample 3: Conditional Mode")
    print("-" * 70)
    adapter = example_conditional_mode(is_debug_mode=False)
    print("Adapter created with conditional mode")
    
    print("\nExample 4: Settings Integration")
    print("-" * 70)
    example_settings_integration()
    print("Settings integration complete")
    
    print("\nExample 5: Testing Both Modes")
    print("-" * 70)
    example_testing_modes()
    print("Mode testing complete")
    
    print("\n" + "=" * 70)
    print("All examples completed successfully!")
    print("=" * 70)
