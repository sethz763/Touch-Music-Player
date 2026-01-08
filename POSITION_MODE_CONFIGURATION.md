# Engine Adapter Position Mode Configuration

## Overview

Added a configurable mode to the EngineAdapter that controls how elapsed/remaining time is calculated relative to in_frame/out_frame trim markers.

## New API

### Method
```python
def set_engine_position_relative_to_trim_markers(self, enabled: bool) -> None
```

### Parameters
- `enabled` (bool): 
  - **True (default)**: Elapsed/remaining are calculated relative to in_frame/out_frame
  - **False**: Elapsed/remaining are absolute file positions (no trim adjustment to elapsed)

## Modes Explained

### Mode 1: Trimmed Time (Default, `enabled=True`)
- **Elapsed** starts at 0.0 when playback begins at in_frame
- **Remaining** counts down from (out_frame - in_frame) / sample_rate
- Example: Playing bytes 24000-72000 @ 48kHz (0.5-1.5 seconds of file)
  - Trim duration: 1.0 second
  - elapsed goes: 0.0 → 1.0 seconds
  - remaining goes: 1.0 → 0.0 seconds

### Mode 2: Absolute File Position (Optional, `enabled=False`)
- **Elapsed** represents absolute file position (in seconds from file start)
- **Remaining** is calculated as (out_frame - in_frame) / sample_rate
- Example: Same 24000-72000 range (0.5-1.5s in file)
  - Trim duration: 1.0 second (same as mode 1)
  - elapsed goes: 0.5 → 1.5 seconds (file position)
  - remaining goes: 1.0 → 0.0 seconds

## When to Use

### Mode 1: Trimmed Time (Recommended)
- **Use case**: Most applications where you want users to see "time remaining in this clip"
- **Behavior**: Feels natural; playback appears to start from 0:00
- **Best for**: UI display, progress bars, time markers

### Mode 2: Absolute File Position
- **Use case**: Detailed debugging, understanding file position
- **Behavior**: Shows where in the file playback is happening
- **Best for**: Technical diagnostics, synchronized editing with timeline views

## Implementation Details

### Code Location
- **File**: `gui/engine_adapter.py`
- **Setting**: `self._position_relative_to_trim_markers` (instance variable)
- **Calculation**: `_calculate_trimmed_time()` method

### How It Works
1. When time events arrive from the engine, `_calculate_trimmed_time()` is called
2. The method checks `self._position_relative_to_trim_markers`
3. If True: calculates remaining as `(out_frame - in_frame) / sr - elapsed`
4. If False: uses elapsed as-is, remaining stays the same
5. Trimmed remaining is sent to GUI via `cue_time` signal

### Default Behavior
- Default is **True** (trimmed time mode)
- No code changes needed to existing functionality
- Can be switched at runtime via the setter method

## Usage Example

```python
from gui.engine_adapter import EngineAdapter

# Create adapter (default mode = trimmed)
adapter = EngineAdapter(cmd_q, evt_q)

# Switch to absolute position mode for debugging
adapter.set_engine_position_relative_to_trim_markers(False)

# Switch back to trimmed mode
adapter.set_engine_position_relative_to_trim_markers(True)
```

## Testing

The configuration is tested implicitly by existing test suite:
- `test_loop_fix.py` - Tests looped playback time reporting
- `manual_test_audio_editor_jog_scroll.py` - Tests UI time display during playback
- `manual_test_audio_editor_integration.py` - Tests editor integration

To validate both modes, you can:
1. Run tests with default mode (True) - should show trimmed time
2. Modify MainWindow to call `adapter.set_engine_position_relative_to_trim_markers(False)` 
3. Run same tests - should show absolute file position

## Debugging

To enable debug logging of time calculations:
```bash
export STEPD_TRIMMED_TIME_DEBUG=1
python -m app.music_player
```

This will log:
```
[_calculate_trimmed_time] cue=12345678 elapsed=0.5234 in_frame=0 out_frame=96000 sr=48000 mode=trimmed
```

## Backward Compatibility

✅ **Fully backward compatible**
- Default mode (True) preserves existing behavior
- No changes to signal signatures
- Existing code continues to work unchanged
- Can be toggled at runtime without side effects
