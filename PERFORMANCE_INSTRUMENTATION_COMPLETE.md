# Performance Debugging: Complete Instrumentation Summary

## Status: Multi-Level Instrumentation Complete

The bottleneck is in the real GUI, not the test. I've added timing instrumentation at three levels:

### Level 1: Engine Adapter ✅ DONE
- **Location:** `gui/engine_adapter.py`
- **Status:** All adapter operations are FAST (< 1ms)
- **Result:** This is NOT the bottleneck

### Level 2: Button Bank Widget ✅ DONE  
- **Location:** `ui/widgets/button_bank_widget.py`
- **What's timed:**
  - Event routing from adapter to individual buttons
  - Cue started/finished/time/levels event handlers
- **Threshold:** 2.0ms (shows slow handlers)

### Level 3: Sound File Button ✅ DONE
- **Location:** `ui/widgets/sound_file_button.py`  
- **What's timed:**
  - Individual button UI state updates
  - Label updates, fade button visibility
  - Level meter rendering
- **Threshold:** 2.0ms (shows slow handlers)

---

## How It Works

```
Audio Engine Process
        ↓ (sends event via queue)
EngineAdapter._poll_events() ← [TIMED: < 1ms, working well]
        ↓ (emits Qt signal)
ButtonBankWidget._on_adapter_cue_finished() ← [TIMED: routing]
        ↓ (routes to owning button)
SoundFileButton._on_cue_finished() ← [TIMED: UI update]
        ↓ (updates UI state)
self.update() → Qt repaints button
```

When multiple cues finish simultaneously:
- 1st finish: ~1ms (routing) + ~X ms (button update) = X+1ms
- 2nd finish: ~1ms (routing) + ~X ms (button update) = X+1ms  
- 3rd finish: ~1ms (routing) + ~X ms (button update) = X+1ms
- ...
- Total: ~N*X ms (where N = number of finishing cues)

If X > 5ms, then N cues finishing = 5N+ ms of blocking → GUI freeze

---

## Running the Full Test

### Step 1: Prepare
```bash
cd c:\Users\Seth Zwiebel\OneDrive\Documents\step_d_audio_fix
# Your normal workflow to start the GUI
```

### Step 2: Load Audio
1. Open the main GUI
2. File → Open (load 5+ audio files)
3. Drag them into the button grid

### Step 3: Trigger Freeze Scenario
```
1. Play cue 1, 2, 3, 4, 5 (all simultaneously)
2. While playing:
   - Select cue 1 and fade out
   - Select cue 2 and fade out
   - Select cue 3 and fade out  
   - Select cue 4 and fade out
   - Select cue 5 and fade out
3. IMMEDIATELY while fading:
   - Select empty button and play a NEW cue
```

### Step 4: Watch Console

You should see `[PERF]` messages like:
```
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.34ms cue_id=abc reason=eof
[PERF] SoundFileButton._on_cue_finished: 2.15ms cue_id=abc reason=eof
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.41ms cue_id=def reason=eof
[PERF] SoundFileButton._on_cue_finished: 18.45ms cue_id=def reason=eof  ← SLOW!
```

### Step 5: Report Results

**Share the slowest operations from the console output.**

Examples of what we're looking for:
- Which operations exceed 2ms?
- Do they happen one at a time or all at once?
- Is it consistent or variable?
- Does it get worse with more simultaneous cues?

---

## Expected Findings

### Most Likely: Button Finish Handler is Slow
```
[PERF] SoundFileButton._on_cue_finished: 12-20ms per cue
```

**Why it blocks:**
1. `self._stop_flash()` - Might be expensive
2. `self._update_label_text()` - Font metrics calculation
3. `self.update()` - Qt repaint triggered
4. Multiple state changes

**With 5 cues finishing:** 5 × 15ms = 75ms blocked GUI

### Alternative: Level Meter Updates are Slow
```
[PERF] SoundFileButton._on_cue_levels: 5-10ms per update
```

**Why it blocks:**
1. Level meter rendering
2. Multiple `self.update()` calls
3. Meter value calculations
4. Happens 10-20x per second

**Impact:** Continuous 5-10ms delays add up to stuttering

### Less Likely: Button Lookup is Slow
```
[PERF] ButtonBankWidget._on_adapter_cue_finished: 1-5ms per event
```

**Why it blocks:**
1. Linear search through 24 buttons
2. Should be < 0.5ms normally
3. If slow, something in button comparison is expensive

---

## Instrumentation Details

### ButtonBankWidget Timing
```python
def _on_adapter_cue_finished(self, cue_id: str, cue_info: object, reason: str) -> None:
    start = time.perf_counter()
    # ... find and call button handler ...
    elapsed = (time.perf_counter() - start) * 1000
    if elapsed > self._slow_threshold_ms:
        print(f"[PERF] ButtonBankWidget._on_adapter_cue_finished: {elapsed:.2f}ms cue_id={cue_id} reason={reason}")
```

**What's measured:** Time to route event to owning button

### SoundFileButton Timing
```python
def _on_cue_finished(self, cue_id: str, cue_info: object, reason: str) -> None:
    start = time.perf_counter()
    # ... update button state, UI, labels, etc ...
    elapsed = (time.perf_counter() - start) * 1000
    if elapsed > 2.0:
        print(f"[PERF] SoundFileButton._on_cue_finished: {elapsed:.2f}ms cue_id={cue_id} reason={reason}")
```

**What's measured:** Time to update button's internal state AND Qt UI

---

## What We'll Do With Results

### If finish handler is slow (likely):
1. Profile the `_on_cue_finished()` method
2. Identify which line takes the most time:
   - `_stop_flash()`?
   - `_update_label_text()`?
   - `self.update()`?
   - Something else?
3. Optimize that specific operation
4. Examples:
   - Defer label update to next frame
   - Cache font metrics
   - Batch multiple updates
   - Use simpler state changes

### If level meter is slow:
1. Reduce update frequency (throttle)
2. Simplify meter rendering
3. Cache meter calculations
4. Only update visible meters

### If button lookup is slow:
1. Use dictionary instead of list search
2. Cache button→cue_id mapping
3. Use hash lookup instead of loop

---

## Files Modified

1. **gui/engine_adapter.py**
   - Added `import time`
   - Instrumented: play_cue, stop_cue, fade_cue, _poll_events, _dispatch_event
   - Result: All operations < 1ms ✅

2. **ui/widgets/button_bank_widget.py**
   - Added `import time`
   - Instrumented: _on_adapter_cue_started/finished/time/levels
   - Measures: Event routing efficiency

3. **ui/widgets/sound_file_button.py**
   - Already had `import time`
   - Instrumented: _on_cue_started/finished/time/levels
   - Measures: Button UI update efficiency

---

## Quick Disable (When Done)

To disable all performance messages:

**engine_adapter.py:**
```python
self._slow_threshold_ms = 1000.0  # Only show > 1 second
```

**button_bank_widget.py:**
```python
self._slow_threshold_ms = 1000.0
```

**sound_file_button.py:** (inside methods, change if statement)
```python
if elapsed > 1000.0:  # Never triggers
```

Or just comment out the `print()` statements.

---

## Next Actions

1. **Run the real GUI with the fade scenario**
2. **Capture all `[PERF]` messages**
3. **Share the output** (especially the slowest operations)
4. **I'll identify the exact bottleneck** and optimize it

The instrumentation is complete and ready to pinpoint the exact source of the lock-up!

