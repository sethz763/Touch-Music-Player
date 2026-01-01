# Auto-Fade Bug Fix: Root Cause Analysis

## The Problem
When multiple tracks were playing in layered mode and then auto-fade mode was enabled, **only 1 cue would fade out** instead of all the other cues fading out when a new cue started.

## Root Cause
The engine was filtering out cues from the auto-fade list using:
```python
old_cues = [c for c in list(self.active_cues.keys()) if c != cue_id and c not in self._fade_requested]
```

This line excluded any cue that was already marked as "fading requested" (`_fade_requested` set). 

### Why This Was a Problem
1. When cue_1 started, cue_0 was added to `_fade_requested` and a fade command was sent
2. When cue_2 started before cue_0's fade completed, cue_0 was **filtered out** because it was in `_fade_requested`
3. Result: Only cue_1 got faded, not cue_0

With 4+ cues playing in sequence, only the most-recently-fading cue would be re-faded, leaving older cues still playing.

## The Fix
**Remove the filter** - fade ALL other active cues, regardless of whether they're already in `_fade_requested`:

```python
# OLD: filtered out already-fading cues
old_cues = [c for c in list(self.active_cues.keys()) if c != cue_id and c not in self._fade_requested]

# NEW: fade all other cues
old_cues = [c for c in list(self.active_cues.keys()) if c != cue_id]
```

### Why This Works
- The output process gracefully handles duplicate fade commands by replacing the existing fade envelope
- Multiple fade commands for the same cue simply create a new fade starting from the current gain
- This ensures ALL cues fade when a new cue starts, regardless of previous fade state

## Testing
The fix was validated with a test script that:
1. Started 4 cues simultaneously in layered mode
2. Enabled auto-fade mode
3. Started a 5th cue

**Before Fix:**
- Only 1 cue would be requested to fade (logged as `old_cues_to_fade=1`)
- Other cues continued playing at full volume

**After Fix:**
- All 4 existing cues are now requested to fade (logged as `old_cues_to_fade=4`)
- Fade commands sent for all: `sent=4`
- Output process receives all fade commands and creates proper fade envelopes

## Related Changes
Also improved:
1. **Non-blocking queue operations** - Using `put()` with 100ms timeout instead of `put_nowait()` to prevent command loss
2. **Aggressive timeout** - Reduced refade timeout from 500ms+ to 100ms so stuck cues force-stop quickly
3. **Comprehensive logging** - Added debug output to track fade requests, queue operations, and envelope completions
