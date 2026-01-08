# Position Mode Configuration - Implementation Summary

## What Was Added

A new configurable API in the EngineAdapter that allows switching between two time calculation modes:

### New Method
```python
adapter.set_engine_position_relative_to_trim_markers(bool)
```

### Location
- **File**: `gui/engine_adapter.py`
- **Instance Variable**: `self._position_relative_to_trim_markers` (default: `True`)
- **Calculation Method**: `_calculate_trimmed_time()` (lines 585-644)

## Implementation Details

### Mode 1: Trimmed Time (Default, `enabled=True`)
Elapsed time starts at 0 and counts up to the trimmed duration. This is the most intuitive for UI display.

**Formula**:
```
remaining = trimmed_total - elapsed
  where trimmed_total = (out_frame - in_frame) / sample_rate
```

**Example**: Playing 0.5-1.5s range of a file @ 48kHz
- Trimmed duration: 1.0s
- elapsed: 0 → 1.0s (counts from 0)
- remaining: 1.0 → 0s (counts down)

### Mode 2: Absolute File Position (`enabled=False`)
Elapsed time shows the actual file position (not adjusted for in_frame). This is useful for debugging and understanding where in the file playback actually is.

**Formula**:
```
remaining = (out_frame / sample_rate) - elapsed
  where elapsed is the absolute file position
```

**Example**: Playing 0.5-1.5s range of a file @ 48kHz
- Trimmed duration: 1.0s (same as mode 1)
- elapsed: 0.5 → 1.5s (shows file position)
- remaining: 1.0 → 0s (still counts down correctly)

## Why This Helps

This configuration was added to resolve potential ambiguity in how time is calculated during trimmed playback. With explicit modes, developers can:

1. **Debug time counter issues** by switching modes to understand the data flow
2. **Choose the right time semantics** for their application
3. **Verify engine behavior** by comparing mode outputs
4. **Test both interpretations** of elapsed time

## Testing

Two comprehensive tests are included:

### 1. Unit Test: `test_position_modes.py`
Tests both calculation modes with various elapsed time values:
```bash
python test_position_modes.py
```

**Results**: ✅ All tests pass
- Mode 1 (trimmed): Validates elapsed counts from 0 to duration
- Mode 2 (absolute): Validates elapsed shows actual file position

### 2. Existing Integration Tests
- `test_loop_fix.py` - ✅ Still passes (default mode works)
- `manual_test_audio_editor_jog_scroll.py` - Compatible
- `manual_test_audio_editor_integration.py` - Compatible

## Code Changes

### Files Modified
1. **gui/engine_adapter.py**
   - Added instance variable in `__init__`: `_position_relative_to_trim_markers`
   - Added setter method: `set_engine_position_relative_to_trim_markers()`
   - Updated `_calculate_trimmed_time()` with mode branching logic

### Files Created
1. **test_position_modes.py** - Unit test for both modes
2. **POSITION_MODE_CONFIGURATION.md** - User documentation

## Backward Compatibility

✅ **Fully backward compatible**
- Default is trimmed time mode (existing behavior)
- No changes to signal signatures
- No required code changes in existing GUI code
- Can be toggled at runtime: `adapter.set_engine_position_relative_to_trim_markers(False)`

## Usage Example

```python
from gui.engine_adapter import EngineAdapter

# Create adapter (defaults to trimmed time mode)
adapter = EngineAdapter(cmd_q, evt_q)

# Optional: Switch to absolute position mode for debugging
adapter.set_engine_position_relative_to_trim_markers(False)

# Play a cue with trimming
adapter.play_cue(
    file_path="/path/to/audio.wav",
    in_frame=24000,  # Start at 0.5s
    out_frame=72000  # End at 1.5s
)

# In trimmed mode: elapsed 0.0→1.0, remaining 1.0→0.0
# In absolute mode: elapsed 0.5→1.5, remaining 1.0→0.0
```

## Next Steps (Optional)

To use this in the GUI for debugging:

1. Add a settings option to toggle position mode
2. Call `set_engine_position_relative_to_trim_markers()` from settings
3. Observe time display changes based on mode
4. Use this to diagnose any remaining time calculation issues

Example in MainWindow:
```python
def on_position_mode_changed(self, enabled: bool):
    """User toggled position mode in settings."""
    self.engine_adapter.set_engine_position_relative_to_trim_markers(enabled)
```

## Debugging

Enable debug logging to see what's happening:
```bash
export STEPD_TRIMMED_TIME_DEBUG=1
python -m app.music_player
```

Output:
```
[_calculate_trimmed_time] cue=abc12345 elapsed=0.5000 in_frame=24000 out_frame=72000 sr=48000 mode=trimmed
[_calculate_trimmed_time] cue=abc12345 elapsed=1.0000 in_frame=24000 out_frame=72000 sr=48000 mode=trimmed
```

This allows you to verify the calculation matches your expectations before finalizing which mode to use.
