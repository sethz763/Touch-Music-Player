# Position Mode Configuration - User Guide

## Quick Start

### Default Behavior (No Changes Needed)
The new position mode feature is fully backward compatible. By default, it uses **trimmed time mode**, which is the existing behavior. No code changes are required.

### Enable Absolute Position Mode
```python
from gui.engine_adapter import EngineAdapter

adapter = EngineAdapter(cmd_q, evt_q)

# Switch to absolute position mode
adapter.set_engine_position_relative_to_trim_markers(False)

# Switch back to trimmed mode (default)
adapter.set_engine_position_relative_to_trim_markers(True)
```

## Two Calculation Modes

### Mode 1: Trimmed Time (Default) ✅
**Best for**: Normal playback UI, progress bars, user-facing time display

```
Scenario: Playing 0.5s - 1.5s of a file (1.0s duration)
├─ Playback starts
│  ├─ elapsed: 0.0s
│  ├─ remaining: 1.0s
│
├─ Mid-playback (0.5s of trimmed range)
│  ├─ elapsed: 0.5s
│  ├─ remaining: 0.5s
│
└─ Playback ends
   ├─ elapsed: 1.0s
   ├─ remaining: 0.0s
```

**Characteristics**:
- Elapsed starts at 0
- Remaining counts down
- Natural feeling for users
- Good for UI progress bars

### Mode 2: Absolute File Position (Optional) 🔧
**Best for**: Debugging, synchronization, understanding file position

```
Scenario: Playing 0.5s - 1.5s of a file (1.0s duration)
├─ Playback starts at file position 0.5s
│  ├─ elapsed: 0.5s (actual file position)
│  ├─ remaining: 1.0s
│
├─ Mid-playback at file position 1.0s
│  ├─ elapsed: 1.0s (actual file position)
│  ├─ remaining: 0.5s
│
└─ Playback ends at file position 1.5s
   ├─ elapsed: 1.5s (actual file position)
   ├─ remaining: 0.0s
```

**Characteristics**:
- Elapsed shows file position, not trimmed position
- Remaining still counts down correctly
- Useful for understanding engine behavior
- Good for diagnostics

## When to Use Each Mode

| Use Case | Mode | Reason |
|----------|------|--------|
| Normal playback | Trimmed | Intuitive for users |
| Progress bars | Trimmed | Shows relative progress within clip |
| Time display | Trimmed | Natural starting point (0:00) |
| Debugging | Absolute | Shows actual file position |
| Editor sync | Absolute | Knows exact file location |
| Looping | Either | Both work correctly |
| Trimmed playback | Either | Both handle trim correctly |

## Troubleshooting

### I see "0.5s elapsed" at the start of trimmed playback

**This means**: Absolute mode is enabled

**Solution**: 
```python
adapter.set_engine_position_relative_to_trim_markers(True)
```

### I see "0.0s elapsed" but the file position is at 0.5s

**This means**: Trimmed mode is enabled

**This is correct** if you're playing from 0.5s mark. The trimmed position correctly shows 0.

### Both modes show the same values

**This means**: No trimming is active (in_frame=0, out_frame=None)

**This is correct** - without trimming, both modes are identical.

## Integration Checklist

- [x] ✅ Method added: `set_engine_position_relative_to_trim_markers(bool)`
- [x] ✅ Default mode: Trimmed (backward compatible)
- [x] ✅ Both modes tested: `test_position_modes.py` ✅ PASS
- [x] ✅ Integration tests pass: `test_loop_fix.py` ✅ PASS
- [x] ✅ Documentation provided (this file)
- [ ] Optional: Add UI control if needed
- [ ] Optional: Add environment variable support

## Files Modified

### Core Implementation
- **gui/engine_adapter.py** - Added mode setting and calculation logic

### Tests
- **test_position_modes.py** - Unit test for both modes (NEW)

### Documentation
- **POSITION_MODE_CONFIGURATION.md** - Technical documentation (NEW)
- **IMPLEMENTATION_NOTES_POSITION_MODES.md** - Implementation details (NEW)
- **POSITION_MODE_INTEGRATION_EXAMPLES.py** - Integration examples (NEW)
- **POSITION_MODE_USER_GUIDE.md** - This file (NEW)

## Architecture

```
┌─────────────────────────────────────────────────┐
│ GUI receives time events from engine            │
└────────────────┬────────────────────────────────┘
                 │
                 v
        ┌────────────────────┐
        │ EngineAdapter      │
        │ _calculate_time()  │
        └────────┬───────────┘
                 │
         ┌───────▼───────┐
         │ Check Mode    │
         └───┬───────────┘
             │
       ┌─────┴──────┐
       │             │
   Trimmed      Absolute
   Time         Position
   │             │
   │ elapsed=0   │ elapsed=file_pos
   │ at start    │ at start
   │             │
   └─────┬───────┘
         │
         v
   emit cue_time signal
   to GUI button
```

## Performance Impact

**None detected** - The mode check is a single boolean comparison:
```python
if self._position_relative_to_trim_markers:
    # Mode 1
else:
    # Mode 2
```

Both modes use identical computations, just with different inputs. No overhead.

## Future Enhancements (Optional)

1. **UI Control**: Add settings dialog checkbox
2. **Env Var**: `STEPD_POSITION_MODE=trimmed|absolute`
3. **Per-Cue Mode**: Allow different cues to use different modes
4. **Visualization**: Show current mode in status bar
5. **Auto-Detection**: Choose mode based on operation type

## Questions & Answers

**Q: Will this break my existing code?**
A: No. The default mode matches existing behavior exactly.

**Q: Can I switch modes during playback?**
A: Yes, switching is safe. It affects the next `_calculate_trimmed_time()` call.

**Q: Which mode should I use?**
A: Use the default (trimmed). Switch to absolute only if debugging.

**Q: How do I know which mode is active?**
A: Check `adapter._position_relative_to_trim_markers` or enable debug logging:
```bash
export STEPD_TRIMMED_TIME_DEBUG=1
```

**Q: What if I need both modes simultaneously?**
A: Create two adapters, each with different mode settings.

## Support

For issues or questions:
1. Check debug output: `STEPD_TRIMMED_TIME_DEBUG=1`
2. Run unit test: `python test_position_modes.py`
3. Review integration examples: `POSITION_MODE_INTEGRATION_EXAMPLES.py`
4. Check implementation details: `IMPLEMENTATION_NOTES_POSITION_MODES.md`
