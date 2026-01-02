# GUI Performance Debugging - Widget Level Instrumentation

## Update: Widget-Level Timing Added

The adapter timing showed everything is fast (< 5ms). The bottleneck is in the actual button widgets! 

I've added comprehensive timing instrumentation to:

### 1. **ButtonBankWidget** (`ui/widgets/button_bank_widget.py`)
- `_on_adapter_cue_started()` - Routes start events to buttons
- `_on_adapter_cue_finished()` - Routes finish events to buttons (most critical path)
- `_on_adapter_cue_time()` - Routes time updates to buttons
- `_on_adapter_cue_levels()` - Routes level meter data to buttons

### 2. **SoundFileButton** (`ui/widgets/sound_file_button.py`)
- `_on_cue_started()` - Updates button UI when cue starts
- `_on_cue_finished()` - Resets button UI when cue finishes
- `_on_cue_time()` - Updates time display
- `_on_cue_levels()` - Updates level indicators and meters

---

## Expected Output

When you trigger multiple cues fading out + new cue starting, watch the console for:

```
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.45ms cue_id=abc123 reason=eof
[PERF] SoundFileButton._on_cue_finished: 1.23ms cue_id=abc123 reason=eof
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.42ms cue_id=def456 reason=eof
[PERF] SoundFileButton._on_cue_finished: 0.89ms cue_id=def456 reason=eof
[PERF] ButtonBankWidget._on_adapter_cue_finished: 2.34ms cue_id=ghi789 reason=eof
[PERF] SoundFileButton._on_cue_finished: 8.56ms cue_id=ghi789 reason=eof  <-- SLOW!
```

---

## What to Look For

### Good Performance
- All `[PERF]` messages < 2ms
- Even with 5+ simultaneous cues, total time should be < 10-15ms

### Bad Performance
- Any single handler > 2ms (especially `_on_cue_finished`)
- Multiple slow handlers stacking up (locks GUI)
- Cumulative time > 16ms (would skip a frame at 60fps)

---

## Example Scenarios

### Scenario A: Slow Finish Handling (LIKELY)
```
[PERF] SoundFileButton._on_cue_finished: 15.67ms cue_id=abc123 reason=eof
[PERF] SoundFileButton._on_cue_finished: 14.23ms cue_id=def456 reason=eof
[PERF] SoundFileButton._on_cue_finished: 16.89ms cue_id=ghi789 reason=eof
```

**Diagnosis:** Button finish handlers taking 15ms each!
**Impact:** With 5 cues, that's 75ms of blocking = 4+ frames frozen
**Likely Cause:** 
- `_stop_flash()` method
- `_update_label_text()` method  
- Multiple `self.update()` calls
- Label metrics recalculation
- Complex state changes

### Scenario B: Slow Level Updates
```
[PERF] SoundFileButton._on_cue_levels: 8.34ms cue_id=abc123
[PERF] SoundFileButton._on_cue_levels: 7.89ms cue_id=abc123
[PERF] SoundFileButton._on_cue_levels: 8.12ms cue_id=abc123
```

**Diagnosis:** Level meter updates taking 8ms each
**Impact:** Happens 10-20x per second = constant CPU load
**Likely Cause:**
- `level_meter_left.setValue()` or `level_meter_right.setValue()` is expensive
- `self.update()` triggering full repaint
- NumPy calculations in hot path

### Scenario C: Slow Button Routing
```
[PERF] ButtonBankWidget._on_adapter_cue_finished: 2.45ms cue_id=abc123 reason=eof
```

**Diagnosis:** Finding the button owner is slow
**Likely Cause:** Button lookup in large button array
**Impact:** O(N) search through 24 buttons (should be < 0.5ms)

---

## How to Run with Full Instrumentation

1. **Start the real GUI normally**
2. **Set up scenario:**
   - Load 5+ audio files into buttons
   - Play them all simultaneously
   - Let them fade out while starting a new cue
3. **Watch console for `[PERF]` messages**
4. **Note the slowest operation** - that's the target for optimization

---

## Threshold

The threshold is set to **2.0ms** for widget-level handlers.

Why 2ms?
- At 60fps, each frame is ~16.67ms
- 2ms = ~1/8 of a frame
- Multiple slow handlers add up quickly

If you want more detail, lower it:
```python
# In ButtonBankWidget.__init__ and SoundFileButton.__init__:
self._slow_threshold_ms = 1.0  # Show everything > 1ms
```

Or less detail:
```python
self._slow_threshold_ms = 5.0  # Only show really slow (> 5ms)
```

---

## Next Steps

1. **Run the real GUI** with some loaded audio files
2. **Trigger the freeze scenario:** Multiple cues fading + new cue
3. **Copy `[PERF]` output from console** and share
4. **Identify the slowest handler** - we'll optimize that specific path

Once we know which operation is slow, we can drill into that code and find the exact bottleneck (likely paint event, label update, or meter rendering).

---

## Test Steps (Detailed)

```
1. Start main GUI
2. Load 5 audio files (File → Open)
3. Add them to buttons (drag to button grid)
4. Click button 1, wait for playback
5. Click button 2, wait for playback  
6. Click button 3, wait for playback
7. Click button 4, wait for playback
8. Click button 5, wait for playback
9. Now they're all playing...
10. Select button 1, click Fade Out (or Stop with fade)
11. Immediately after fade starts, select button 6 and play new file
12. WATCH CONSOLE and OBSERVE GUI FREEZE TIMING
13. Repeat 2-3 times to get clear pattern
14. Share [PERF] messages from console
```

---

## If No [PERF] Messages Appear

If everything shows < 2ms, the bottleneck might be:
- Qt rendering/paint events (use Qt Profiler)
- Audio service event generation (add instrumentation there)
- OS scheduler delay (system load issue)
- Disk I/O (file access during playback)

In that case, we'll need to:
1. Add instrumentation to audio_service.py
2. Profile with cProfile or py-spy
3. Check system resource usage (CPU, disk, memory)

