# Ring Buffer Loop Architecture: Implementation Summary

## Overview

The audio looping system has been refactored to implement a **true ring buffer pattern** where the decoder process proactively handles loop boundaries, eliminating race conditions and frame loss that occurred between the decode and output processes.

## Key Changes

### 1. Decoder Process (`decode_process.py`)

#### New Function: `_seek_to_loop_boundary()`
- Replaces reactive "restart after EOF" with proactive "seek before boundary"
- Called when decoder approaches the loop boundary (frame count based)
- Immediately resets decoder state, permitting seamless continuation
- Cleaner than the old `_restart_cue_decoder_fast()` with better error handling

#### New State: `loop_seeked` Dictionary
```python
loop_seeked[cue_id] = True  # Set when we've proactively seeked
```
Tracks whether we've already seeked for the next iteration. When True:
- Decoder is filling buffer from the beginning again
- Output process continues draining (no EOF visible)
- No race condition possible

#### Modified Decode Loop
Changed from reactive EOF-based restart to **proactive boundary-based approach**:

**BEFORE**: 
```
Decode frames → Hit EOF → Mark eof=True → Output detects → Output clears ring → 
Race condition: new frames arrive before clear completes
```

**AFTER**:
```
Decode frames → (lookahead) → Approaching boundary → Seek to start → 
Continue decoding from beginning → Output never sees EOF for looping cues → 
Seamless buffer flow
```

**Key Logic**:
```python
LOOKAHEAD_WINDOW = msg0.block_frames * 2  # When to start seeking

remaining_frames = msg0.out_frame - st["decoded_frames"]

# If approaching boundary AND not yet seeked:
if remaining_frames <= LOOKAHEAD_WINDOW and not loop_seeked.get(cue_id):
    mark for proactive seek

# When we hit the exact boundary:
if st["decoded_frames"] >= msg0.out_frame and not loop_seeked.get(cue_id):
    execute seek now
    set loop_seeked[cue_id] = True
```

#### Frame Sending
- **Looping cues**: NEVER send `eof=True` to output
  - Output sees continuous stream (true ring buffer behavior)
  - No special handling needed on output side
- **Non-looping cues**: Behavior unchanged (send eof when done)

#### Removed
- `loop_restart_times` tracking (no longer needed)
- `just_restarted` flag (proactive approach eliminates need)
- Complex pre-buffering logic (implicit now)
- EOF-triggered restart logic

---

### 2. Output Process (`output_process.py`)

#### Simplified Ring Clearing Logic
**BEFORE**: Special handling for looping cues
```python
if is_looping:
    # Reset ring for next iteration
    ring.eof = False
    ring.q.clear()
    ring.frames = 0
    ...
```

**AFTER**: No special looping logic needed
```python
if ring.finished_pending:
    # Only handle true EOF (non-looping cues)
    event_q.put(("finished", cue_id))
    rings.pop(cue_id, None)
    ...
```

#### Why This Works
- Decoder never sends `eof=True` for looping cues
- `ring.finished_pending` is only set when callback returns `done=True`
- `done=True` only when `(filled == 0 and eof and frames == 0)`
- For looping, eof stays False → finished_pending never sets → ring is never cleared
- Output just keeps pulling frames that decoder keeps providing

#### Looping Cues Behavior
- Output process continues draining ring normally
- Decoder seamlessly provides next iteration frames
- No stutter, no gaps, no frame loss
- Ring is never explicitly cleared (not needed for looping)

---

## Frame Loss Prevention: How It Works

### The Root Cause (Fixed)
In the old approach:
1. Decoder hits EOF, sends final frames with `eof=True`
2. Output callback consumes frames, detects EOF
3. Callback sets `finished_pending = True`
4. Main loop detects `finished_pending`, clears ring
5. **RACE**: Decoder thread sends next iteration frames BEFORE ring is cleared
6. **BUG**: Ring clear discards those pre-buffered frames → Frame loss

### The Solution
1. Decoder approaches boundary (frame count check)
2. **BEFORE** sending final packet, decoder seeks to start
3. Decoder continues sending frames (now from beginning)
4. Output never knows a restart happened
5. Ring stays full, callback drains continuously
6. ✅ No gap, no race, no loss

### With Lookahead Window
- Proactive seeking happens BEFORE boundary, not at boundary
- Output has time to drain before seeking
- Seek can happen during "silence" between output requests
- More deterministic, less jittery

---

## Benefits

| Aspect | Before | After |
|--------|--------|-------|
| **Frame Loss** | ❌ Lost frames on loop | ✅ Lossless looping |
| **Process Coordination** | Complex (multiple flags) | Simple (single `loop_seeked` flag) |
| **Ring Behavior** | Cleared/reset on loop | Continuous flow (true ring) |
| **Race Conditions** | EOF → clear → pre-buffer race | Proactive seek eliminates race |
| **Code Complexity** | Defensive, many special cases | Straightforward proactive approach |
| **Latency** | Seek after final frame, then buffer | Seek during LOOKAHEAD_WINDOW |
| **Multi-cue Support** | Works but racy | Clean, independent per-cue |

---

## Logging Changes

### Old Prefixes (Removed)
- `[DEBUG-LOOP]` - old reactive restart  
- `[DEBUG-EOF]` - EOF detection  
- `[SAMPLE-RING-CLEAR]` - explicit clearing

### New Prefixes
- `[RING-PROACTIVE]` - approaching boundary, will seek
- `[RING-BOUNDARY]` - hit boundary, seeking now
- `[RING-SEEK]` - seek operation details
- `[RING-SEND]` - sending frames (with `looped=` flag)
- `[RING-ITERATION]` - iteration tracking
- `[RING-DISCARD]` - post-seek tolerance discard
- `[RING-ERROR]` - error conditions
- `[RING-REMOVE]` - cue removal

Ring-based logging is more concise and directly reflects the true ring buffer architecture.

---

## Testing Recommendations

### 1. Frame Count Validation
```
[RING-ITERATION] Cue A: Starting iteration 1
[RING-SEND] Cue A: Sending 8192 frames (decoded_total=8192, looped=False)
[RING-SEND] Cue A: Sending 8192 frames (decoded_total=16384, looped=False)
...
[RING-BOUNDARY] Cue A: Hit boundary, seeking for loop

[RING-ITERATION] Cue A: Starting iteration 2
[RING-SEND] Cue A: Sending 8192 frames (decoded_total=8192, looped=True)
```
✅ If iteration 2 also decodes same total frames as iteration 1, no loss

### 2. Ring Behavior
- Ring should never empty during playback (for looping)
- LOOKAHEAD_WINDOW should prevent seeking while output is actively draining
- No log lines showing ring going to 0 frames mid-loop

### 3. Seek Timing
- Seeks should happen during quiet times (when output is between blocks)
- No disruption to audio playback
- Multiple seeks per test show consistent timing

### 4. Multi-Cue
- Each cue maintains independent `loop_seeked` state
- No frame loss with simultaneous multiple loops
- Each cue's iteration count increments correctly

---

## Migration Notes for Other Code

### Changes to Expect
1. **No EOF events for looping cues** - Output process no longer sends "finished" for looping
2. **Looping is now silent** to output - No restart events, just continuous frames
3. **Old state flags gone** - `just_restarted`, `loop_restart_times` no longer present

### For Audio Engine (`audio_engine.py`)
- If code listened for "looped" events: still works, decoder still sends them
- If code checked cue state for "restarting": loop is now invisible to engine
- Loop restart is decoder-internal, not visible to higher layers

### For GUI Code
- Can still listen for "looped" events if tracking iterations needed
- Or can monitor cue time/position and detect loop boundaries itself
- No forced updates needed

---

## Future Optimizations

1. **Adaptive Lookahead**: Size window based on decode performance
2. **Thread Affinity**: Pin decoder to different core than output for better cache
3. **Lock-Free Buffer**: Use atomic ops instead of queue for faster seeking
4. **Preload Optimization**: Seek could optionally pre-decode multiple blocks
5. **Seeks on Background**: Could queue seeks asynchronously if decoding is fast enough

---

## File Changes Summary

### `engine/processes/decode_process.py`
- Removed: `_restart_cue_decoder_fast()`, `loop_restart_times`
- Added: `_seek_to_loop_boundary()`, `loop_seeked` dict
- Modified: Main decode loop with proactive seeking logic
- Changed: EOF handling for looping cues (never send)

### `engine/processes/output_process.py`
- Removed: Complex ring clearing logic for looping cues
- Simplified: `finished_pending` handling (no special loop case)
- No functional change: Ring draining and callback logic unchanged

### Documented (for reference)
- `LOOP_ARCHITECTURE_ANALYSIS.md` - Full technical analysis and rationale
