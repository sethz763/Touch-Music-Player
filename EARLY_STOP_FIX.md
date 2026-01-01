# Early Cue Stopping Bug Fix

## Problem
Cues were stopping prematurely during multi-cue playback, especially when applying fade-out effects. The logs showed:
- `[CALLBACK-DONE] cue=xxx done=True filled=0 eof=True frames=0` appearing before all audio was consumed
- `refade_pending` → `refade_stuck_cue` → `force_removed_stuck_cue` sequences indicating cues getting stuck during fade-out

## Root Cause
In `output_process.py`, when a fade envelope completed to silence (fade-out finished), the code was immediately setting `ring.eof = True`. This caused the issue:

1. Fade-out completes → `ring.eof = True` is set (lines 252, 261)
2. Ring still has buffered frames that haven't been consumed by the audio callback
3. Next callback iteration, if no frames are pulled (`filled == 0`), the `done` check triggers:
   ```python
   done = (filled == 0 and self.eof and self.frames == 0 and not self.q)
   ```
4. This sends `[CALLBACK-DONE]` even though the decoder might still have pending frames to deliver

## Solution
Instead of setting `ring.eof = True` when the envelope completes, we now:
1. Pop the envelope from the tracking dict
2. Clear `ring.request_pending` to stop requesting more frames from decoder
3. Send a `DecodeStop` command to the decoder (non-blocking)
4. Let the decoder naturally signal EOF when it finishes processing

This ensures all buffered frames in the ring are consumed before the cue is marked as done.

## Changes Made

### File: `engine/processes/output_process.py`

**Change 1: In callback function (batch mode, ~line 252)**
- Before: `ring.eof = True` when envelope finishes to silence
- After: Request `DecodeStop` instead, let EOF propagate naturally

**Change 2: In callback function (per-sample mode, ~line 261)**
- Before: `ring.eof = True` when envelope finishes to silence
- After: Request `DecodeStop` instead, let EOF propagate naturally

**Change 3: In main loop envelope check (~line 437)**
- Before: `ring.eof = True` when envelope completed to silence
- After: Request `DecodeStop` instead, let EOF propagate naturally

## Expected Behavior
- Cues with fade-out will play all audio to completion before signaling done
- The decoder naturally sends EOF when file is finished
- Ring buffer naturally becomes empty when all frames are consumed
- No more premature cue termination in multi-cue scenarios
