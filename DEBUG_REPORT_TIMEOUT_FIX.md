# Debug Analysis: Clips Stopping After Start During Fade Transitions

## Root Cause Found

When a new cue starts while another is fading out, the NEW cue was being immediately marked as EOF and stopping, resulting in no audio being heard.

### The Bug

In `engine/processes/output_process.py`, the timeout-based cleanup logic had a critical flaw:

```python
# BEFORE (BUGGY):
if ring.request_pending and ring.frames == 0:
    if ring.request_started_at is not None:
        time_pending = current_time - ring.request_started_at
        pcm_age = current_time - ring.last_pcm_time if ring.last_pcm_time else float('inf')
        if time_pending > stuck_timeout_secs and pcm_age > stuck_timeout_secs:
            ring.eof = True  # <-- WRONG! Marks new cues as EOF prematurely
```

**Problem**: When a NEW cue is created, `ring.last_pcm_time` is `None`. The code sets `pcm_age = float('inf')`, which means:
- After 2 seconds of waiting for the first PCM chunk from the decoder, the condition `pcm_age > stuck_timeout_secs` is ALWAYS true
- The ring gets marked EOF even though the decoder may still be working on decoding the file
- Result: The new cue is immediately stopped before any audio plays

### The Fix

Only apply the timeout if we've ALREADY received at least one PCM chunk:

```python
# AFTER (FIXED):
if ring.request_pending and ring.frames == 0 and ring.last_pcm_time is not None:
    if ring.request_started_at is not None:
        time_pending = current_time - ring.request_started_at
        pcm_age = current_time - ring.last_pcm_time
        if time_pending > stuck_timeout_secs and pcm_age > stuck_timeout_secs:
            ring.eof = True  # Only timeout if PCM arrived but then stopped arriving
```

**Logic**: 
- If `last_pcm_time is None`, it means we haven't received ANY PCM yet - the decoder is still working, don't timeout
- If `last_pcm_time is not None`, it means we got PCM before but it stopped - this is a stuck cue, timeout is appropriate

## Debug Logging Added

Enhanced logging was added throughout the pipeline to trace exact event sequence:

1. **output_process.py**:
   - `[TIMEOUT-CLEANUP]` - when timeout occurs
   - `[START-CUE]` - when OutputStartCue received
   - `[DRAIN-ACTIVATE]` - when first PCM arrives and pending start is activated
   - `[STOP-CUE]` - when OutputStopCue received
   - `[ENVELOPE-SILENCE]` - when fade envelope completes to silence

2. **audio_engine.py**:
   - `[ENGINE-PLAY-CUE]` - when DecodeStart sent for new cue
   - `[ENGINE-FORCE-STOP]` - when force-removing stuck cues
   - `[ENGINE-DECODE-ERROR]` - when decode error occurs

## Testing

Run `test_minimal_fade.py` to verify the fix:
- Plays first cue for 2 seconds
- Starts new cue (triggers auto-fade of first)
- Monitors events during transition
- Should show new cue starting without being immediately stopped

## Related Issues Fixed

1. Fixed timeout applying to NEW cues before they receive any PCM
2. Better distinction between "stuck cue" (no PCM for 2s after receiving some) vs "new cue" (waiting for first PCM)
3. Added logging to diagnose similar issues in the future
