# Sample Tracking Guide for Loop Frame Loss Debugging

## Overview
Comprehensive logging has been added to track samples through the encode/decode/output pipeline during looping. This will help identify exactly where samples are being lost.

## Log Message Prefixes and What They Mean

### Decode Process Logs

#### `[SAMPLE-TRACK]` - Iteration Complete
```
[SAMPLE-TRACK] Cue X: Iteration N COMPLETE - decoded 96000 frames
```
- Logged when an iteration finishes (EOF detected)
- Shows total frames decoded in that iteration
- **Expected**: Each iteration should have same number (or very close)
- **Concern**: If iteration 2+ has fewer frames, loss happened during restart

#### `[SAMPLE-DISCARD]` - Post-Seek Discard
```
[SAMPLE-DISCARD] Cue X: Discarding 2400 samples (remaining: 0, pcm_frames: 2400)
```
- Logged when samples are discarded after seek (post-seek tolerance)
- Shows discard amount and remaining frames to discard
- **Expected**: ~2400 samples per loop (~50ms at 48kHz)
- **Concern**: Different discard amounts per iteration

#### `[SAMPLE-SEND]` - Chunk Sent to Output
```
[SAMPLE-SEND] Cue X: Sending 4096 frames (decoded_total=96000, eof=False)
```
- Logged when a chunk is sent to output process
- Shows frames sent and current decoded total
- **Expected**: One or more chunks per iteration summing to total frames
- **Concern**: Chunks sent not matching decoded amount

### Output Process Logs

#### `[SAMPLE-RING-CLEAR]` - Ring Buffer Cleared
```
[SAMPLE-RING-CLEAR] Cue X: Clearing ring - BEFORE: 2 chunks, 4096 frames
[SAMPLE-RING-CLEAR] Cue X: Clearing ring - AFTER: 0 chunks, 0 frames
```
- Logged when ring is cleared for loop restart
- Shows chunks and frames BEFORE and AFTER clear
- **Expected**: Should clear completely (0 chunks, 0 frames after)
- **Concern**: If before shows many frames, that's buffered audio being discarded

#### `[SAMPLE-PCM-IN]` - Incoming PCM Chunk
```
[SAMPLE-PCM-IN] Cue X: Received 2048 frames - Ring BEFORE: 0 frames, AFTER: 2048 frames
```
- Logged when decode process sends a chunk to output
- Shows ring state before and after push
- **Expected**: Frames increase by chunk size
- **Concern**: If frames don't match chunk size, there's a mismatch

#### `[SAMPLE-CONSUME]` - Audio Consumed by Callback
```
[SAMPLE-CONSUME] Cue X: Consumed 2048 frames in callback - Ring AFTER consume: 0 frames remaining
```
- Logged when audio callback consumes frames from ring
- Shows remaining frames after consumption
- **Expected**: Frames decrease, ring gradually empties then refills
- **Concern**: Frames drop more than consumed amount (indicates silent skips)

## Example Good Sequence

```
[SAMPLE-TRACK] Cue A: Iteration 1 COMPLETE - decoded 96000 frames
[SAMPLE-SEND] Cue A: Sending 8192 frames (decoded_total=8192, eof=False)
[SAMPLE-SEND] Cue A: Sending 8192 frames (decoded_total=16384, eof=False)
...continues until...
[SAMPLE-SEND] Cue A: Sending 4096 frames (decoded_total=96000, eof=True)

[DEBUG-LOOP] Cue A: EOF detected, attempting restart
[RESTART-FAST] Seeking to in_frame=0
[SAMPLE-DISCARD] Cue A: Discarding 2400 samples (remaining: 0, pcm_frames: 2400)

[SAMPLE-RING-CLEAR] Cue A: BEFORE: 4 chunks, 16384 frames
[SAMPLE-RING-CLEAR] Cue A: AFTER: 0 chunks, 0 frames

[SAMPLE-SEND] Cue A: Sending 2048 frames (decoded_total=2048, eof=False)
[SAMPLE-PCM-IN] Cue A: Received 2048 frames - BEFORE: 0, AFTER: 2048

[SAMPLE-CONSUME] Cue A: Consumed 2048 frames - Ring AFTER: 0 frames
[SAMPLE-CONSUME] Cue A: Consumed 2048 frames - Ring AFTER: 2048 frames (buffering)

[SAMPLE-TRACK] Cue A: Iteration 2 COMPLETE - decoded 96000 frames  ✓ MATCH!
```

## Red Flags to Look For

### Flag 1: Iteration frame counts differ
```
[SAMPLE-TRACK] Cue A: Iteration 1 COMPLETE - decoded 96000 frames
[SAMPLE-TRACK] Cue A: Iteration 2 COMPLETE - decoded 94000 frames  ❌ Lost 2000 frames!
```
→ Check SAMPLE-SEND logs to see if all frames were sent, or SAMPLE-PCM-IN to see if they arrived

### Flag 2: Discarded amount changes
```
Iteration 1: [SAMPLE-DISCARD] ... Discarding 2400 samples
Iteration 2: [SAMPLE-DISCARD] ... Discarding 0 samples   ❌ Different!
```
→ Indicates resampler or seek state is inconsistent

### Flag 3: Ring has frames when cleared
```
[SAMPLE-RING-CLEAR] Cue A: BEFORE: 0 chunks, 0 frames   ✓ Good
[SAMPLE-RING-CLEAR] Cue B: BEFORE: 8 chunks, 32768 frames  ❌ Lost frames!
```
→ Those buffered frames are being discarded during ring clear

### Flag 4: PCM arrives AFTER ring is cleared (race condition)
```
[SAMPLE-RING-CLEAR] Cue A: AFTER: 0 chunks, 0 frames
[SAMPLE-PCM-IN] Cue A: Received 2048 frames   ← Good timing
```
vs
```
[SAMPLE-RING-CLEAR] Cue A: AFTER: 0 chunks, 0 frames
...other unrelated logs...
[SAMPLE-RING-CLEAR] Cue A: BEFORE: 2 chunks, 4096 frames  ← Pre-buffered data arrived BEFORE clear!
```

## How to Investigate

1. **Run the app** with looping enabled
2. **Copy the console output** or redirect to file: `python -m app.music_player 2>&1 | tee loop_test.log`
3. **Search the log** for the specific cue ID you're testing
4. **Extract relevant section** focusing on iteration 1 vs 2 vs 3
5. **Compare patterns** - do they match the "Good Sequence" above?

## Tracking Data Structure

The decode process maintains this structure per cue per iteration:
```
sample_tracking[cue_id]["iterations"][iteration_num] = {
    "frames_decoded": N,      # Total frames decoded in this iteration
    "frames_discarded": N,    # Total frames discarded after seek
    "frames_sent": N,         # Total frames sent to output process
    "frames_consumed": N      # (tracked in output process via callback logs)
}
```

You can extend this to print a summary at the end:
```
[SAMPLE-SUMMARY] Cue X Iteration 2:
  Decoded:  96000 frames
  Discarded: 2400 frames
  Sent:     93600 frames
  (Expected: decoded - discarded = sent)
```

## Notes

- All times are in microseconds for precision
- The logging is comprehensive but CPU-safe (mostly string concatenation)
- If you see stuttering or performance issues, disable the `[SAMPLE-*]` logs and keep only the `[DEBUG-*]` logs
