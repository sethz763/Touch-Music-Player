# Looping Fix - Complete

## Problem
Looped cues were only looping **once** and then stopping with EOF, instead of continuing to loop indefinitely.

**Root Cause:** The looping condition in the decoder worker was `if job.cmd.loop_enabled and job.loop_count == 0:` which only allowed looping when `loop_count == 0`. After the first loop, `loop_count` became 1, making the condition False and causing the cue to terminate.

## Solution
Changed the looping condition from:
```python
if job.cmd.loop_enabled and job.loop_count == 0:
```

To:
```python
if job.cmd.loop_enabled:
```

This allows unlimited looping as long as `loop_enabled=True` in the PlayCueCommand.

## Files Modified
- `engine/processes/decode_process_pooled.py` - Line 218: Removed the `loop_count == 0` check from the looping condition

## Test Results
- Looped cue with `loop_enabled=True` successfully remained active for 26+ seconds
- No premature EOF or unexpected removal
- Cue can be stopped manually via `stop_cue()` command
- Compatible with multitrack mode (multiple concurrent looping cues)

## Behavior
- **Before Fix**: Cues looped exactly once, then finished with EOF
- **After Fix**: Cues loop indefinitely until:
  1. Explicitly stopped via `StopCueCommand`
  2. Manually faded out via `FadeCueCommand`  
  3. Auto-faded by playing a new non-layered cue

The `loop_count` variable is still incremented for tracking/logging purposes but no longer gates the looping behavior.
