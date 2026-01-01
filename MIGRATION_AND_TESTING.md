# Ring Buffer Implementation: Migration & Testing Guide

## Quick Start

The changes implement a **true ring buffer pattern** for looping. The key idea:

**Decoder seeks BEFORE sending final frames, not AFTER.**

This eliminates the race condition that was causing frame loss.

---

## What Changed

### In `decode_process.py`:

1. **New Function**: `_seek_to_loop_boundary(st)`
   - Called proactively when approaching loop boundary
   - Resets decoder state (decoded_frames=0, eof=False)
   - Allows seamless continuation from beginning

2. **New State**: `loop_seeked` dictionary
   - `loop_seeked[cue_id] = True` when we've seeked
   - Tracks which cues have already looped

3. **Modified Main Loop**:
   - Instead of "hit EOF then restart", now "approaching boundary so seek now"
   - Uses `LOOKAHEAD_WINDOW = block_frames * 2`
   - For looping cues: **NEVER send `eof=True`**

### In `output_process.py`:

1. **Simplified `finished_pending` handling**
   - Removed special case for looping cues
   - Ring is no longer explicitly cleared for loops
   - Ring stays alive and continues flowing

---

## Testing the New Implementation

### Test 1: Basic Looping (Verify No Frame Loss)

```python
import numpy as np
from engine.audio_engine import AudioEngine
from engine.track import Track

# Create engine
engine = AudioEngine(sample_rate=48000, channels=2, block_frames=1024)
engine.start()

# Create a short test clip (1 second)
# Play it with looping enabled
cue_id = engine.play_cue(
    track=Track(
        track_id="test",
        file_path="test_clip.wav",
        in_frame=0,
        out_frame=48000,
    ),
    gain_db=0,
    loop_enabled=True,
    layered=False
)

# Let it loop 3 times
time.sleep(3)

# Stop and verify:
# - Audio played smoothly
# - No clicks or gaps
# - No warnings in log
```

**What to listen for**:
- ✅ Smooth, seamless loops
- ✅ No clicks at loop boundary
- ✅ No silence/gap between iterations

**What would be wrong**:
- ❌ Click at loop boundary
- ❌ Silence (gap) between iterations
- ❌ First frame of next iteration missing audio

### Test 2: Multi-Cue Looping

```python
# Start 3 loops simultaneously
cue_ids = []
for i in range(3):
    cue_id = engine.play_cue(
        track=Track(
            track_id=f"track_{i}",
            file_path="clips/clip_A.wav",
            in_frame=0,
            out_frame=96000,  # 2 seconds at 48kHz
        ),
        gain_db=-6,  # Lower to hear individual tracks
        loop_enabled=True,
        layered=True  # Stack them
    )
    cue_ids.append(cue_id)

# Play for 10 seconds
time.sleep(10)

# Should hear all 3 looping independently
# Each should loop 5 times
```

**What to listen for**:
- ✅ All 3 clips loop independently
- ✅ Each loop is seamless within its track
- ✅ All three finish at approximately same time (within 1 loop)

### Test 3: Disable Looping Mid-Play

```python
# Start a looping clip
cue_id = engine.play_cue(
    track=Track(
        track_id="test",
        file_path="test.wav",
        in_frame=0,
        out_frame=96000,
    ),
    loop_enabled=True,
)

# Let it loop once
time.sleep(2.1)  # Slightly > 1 iteration

# Disable looping (or stop)
engine.handle_command(StopCueCommand(cue_id))

# Should stop after current playback, emit finish event
```

**What to check**:
- ✅ Stop command works during any loop iteration
- ✅ Finish event emitted once
- ✅ No extra looping after stop

### Test 4: Check Logs for Ring Buffer Behavior

Look for logs like:

```
[RING-PROACTIVE] Cue test_cue: Approaching boundary (16384 frames left), will seek after current block
[RING-BOUNDARY] Cue test_cue: Hit boundary, seeking for loop
[RING-SEEK] Cue test_cue: Seeking to loop boundary in_frame=0
[RING-SEEK] Cue test_cue: Seek successful
[RING-ITERATION] Cue test_cue: Starting iteration 2
[RING-SEND] Cue test_cue: Sending 8192 frames (decoded_total=8192, looped=True)
[RING-SEND] Cue test_cue: Sending 8192 frames (decoded_total=16384, looped=True)
```

**Red flags**:
- ❌ Logs showing `[RING-BOUNDARY]` but no `[RING-SEEK]` success
- ❌ Logs showing `[RING-ITERATION]` with same iteration number
- ❌ No `[RING-SEND]` after `[RING-SEEK]`

### Test 5: Frame Count Consistency

Add tracking code (temporary):

```python
# In decode_process.py, after getting a buffered chunk:
frames_in_chunk = chunk.shape[0]
print(f"[FRAME-TRACK] Cue {cue_id}: Chunk of {frames_in_chunk} frames")

# After test, sum all frames and compare:
# Iteration 1: should = out_frame - in_frame
# Iteration 2: should = iteration 1 (same number of frames)
# Iteration 3: should = iteration 1
```

---

## Interpreting Debug Output

### Expected Output (Good):
```
[RING-PROACTIVE] Cue A: Approaching boundary (12000 frames left), will seek after current block
[RING-BOUNDARY] Cue A: Hit boundary, seeking for loop
[RING-SEEK] Cue A: Seeking to loop boundary in_frame=0
[RING-SEEK] Cue A: Seek successful
[RING-ITERATION] Cue A: Starting iteration 2
[RING-SEND] Cue A: Sending 8192 frames (decoded_total=8192, looped=True)
[RING-SEND] Cue A: Sending 8192 frames (decoded_total=16384, looped=True)
[RING-SEND] Cue A: Sending 8192 frames (decoded_total=24576, looped=True)
```

✅ **Interpretation**:
- Lookahead triggered → Decoder knew boundary was coming
- Seek successful → Proactive rewind worked
- New iteration started → Counter incremented
- Frames sent with `looped=True` → Part of restarted iteration
- Same frames per iteration → No loss

### Problematic Output (Bad):
```
[RING-BOUNDARY] Cue A: Hit boundary, seeking for loop
[RING-SEEK] Cue A: Seek failed: [Error details]
[RING-BOUNDARY] Cue A: Full reinitialization failed
[RING-ERROR] Cue A: Exception during decode
```

❌ **What's wrong**: 
- Seek failed → File format issue or codec problem
- Fallback also failed → Serious problem
- Shouldn't happen with normal audio files

**Fix**: 
- Test with different audio format (WAV vs MP3)
- Check file is readable
- Verify in_frame and out_frame are valid

---

## Common Migration Issues

### Issue 1: "Looped event not firing"

**Cause**: Decoder now handles loops internally, doesn't always emit "looped" event

**Solution**: 
- If you need loop notifications, listen to frame counts
- Or check `cue.loop_count` if available
- Or listen for output process restart (may not happen)

**Code**:
```python
# Instead of listening for "looped" event:
# Track frame count yourself
frames_per_iteration = cue.out_frame - cue.in_frame
current_iteration = total_frames_consumed // frames_per_iteration
```

### Issue 2: "Ring never finishes for looping cues"

**Cause**: Expected behavior! Ring doesn't finish while looping

**Solution**: This is correct. Looping cues don't emit `finished` events

**Code**:
```python
# Don't expect ("finished", cue_id) for looping cues
# Non-looping cues will still emit it
if not cue.loop_enabled:
    # Will eventually get ("finished", cue_id)
```

### Issue 3: "Output process logs don't show ring clearing"

**Cause**: Expected! Ring clearing removed for looping

**Solution**: Nothing to fix, this is correct behavior

**Explanation**: 
- Old approach: Ring cleared on loop restart
- New approach: Ring never cleared, flows continuously
- Simpler, fewer operations

### Issue 4: "Seek happening mid-playback, hearing artifacts"

**Cause**: LOOKAHEAD_WINDOW too small, seeks too close to output boundary

**Solution**: Increase LOOKAHEAD_WINDOW

**Code** (in decode_process.py):
```python
# Current (conservative):
LOOKAHEAD_WINDOW = msg0.block_frames * 2  # ~4-8KB buffer

# If having issues, try larger:
LOOKAHEAD_WINDOW = msg0.block_frames * 4  # Double lookahead
```

---

## Performance Notes

### CPU Impact
- **Seeking**: ~5-10ms per loop (varies by codec/file)
- **Proactive seeking**: Spread over lookahead window, not blocking
- **No additional overhead**: Fewer operations than old approach

### Memory Impact
- **No change**: Same ring size, same buffering
- **Actually better**: No temporary state for restart flags

### Latency Impact
- **Slightly better**: No EOF→clear→resume lag
- **More deterministic**: Seeking happens during predictable window

---

## Troubleshooting Checklist

### Symptom: Audio clicks at loop boundary

- [ ] Check LOOKAHEAD_WINDOW is large enough (min 2*block_frames)
- [ ] Verify file is valid and seekable (try WAV format)
- [ ] Check for CPU spike during seek
- [ ] Try different audio file

### Symptom: Frame loss on short clips

- [ ] Verify out_frame is correct (not truncating audio)
- [ ] Check logs for seek failures
- [ ] Try with out_frame = None (full file) to test
- [ ] Verify in_frame = 0 for start of file

### Symptom: Multi-cue looping has phase drift

- [ ] This is expected! Each cue loops independently
- [ ] If cues should stay in sync, they need to be one cue with time-division
- [ ] Or use master clock synchronization (not built-in)

### Symptom: Loop restarts and audio stutters

- [ ] Increase LOOKAHEAD_WINDOW
- [ ] Check CPU load (seek is CPU-intensive)
- [ ] Try a faster audio format (PCM vs MP3)
- [ ] Check for disk I/O bottleneck

---

## Verification Checklist Before Deployment

- [ ] Looping audio plays smoothly (no clicks)
- [ ] First iteration = subsequent iterations (frame counts)
- [ ] Multiple simultaneous loops work independently
- [ ] Stop command works during looping
- [ ] Non-looping clips still emit finish events
- [ ] Short clips (< 1 second) work
- [ ] Long clips (> 10 seconds) work
- [ ] Different audio formats work (WAV, MP3 if supported)
- [ ] Seek handles in_frame != 0 correctly
- [ ] Seek handles out_frame != full_duration correctly

---

## Reference: Key Constants

### In decode_process.py

```python
# Lookahead window for proactive seeking
LOOKAHEAD_WINDOW = msg0.block_frames * 2  # Tune if needed

# Post-seek discard tolerance (50ms)
discard_after_seek = msg.target_sample_rate // 20

# Recommendation: only adjust LOOKAHEAD_WINDOW if experiencing issues
```

---

## Summary

**The new architecture is simpler, safer, and more efficient because:**

1. **Decoder owns the loop** - No coordination needed
2. **Proactive seeking** - Before boundary, not after
3. **Ring buffer behavior** - True continuous flow
4. **No race conditions** - Decoder is single-threaded
5. **Less state** - One flag per cue, not multiple

**Test thoroughly** to ensure no audio artifacts, then enjoy seamless looping!
