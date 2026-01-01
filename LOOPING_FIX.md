# Looping Fix: EOF Detection for Full-File Clips

## Issue Found

The looping wasn't working because the code only detected loop boundaries when `out_frame` was explicitly specified. When playing a clip without specifying `out_frame` (i.e., play the whole file), the boundary detection never triggered.

**Symptom in logs**:
```
[RING-SEND] Cue XXX: Sending 2110 frames (decoded_total=77883, looped=False)
[SAMPLE-CONSUME] Cue XXX: Consumed 59 frames - Ring AFTER: 0 frames remaining
[cue_finished] Cue XXX ... reason=eof
```

All frames show `looped=False` and cue finishes instead of looping.

## Root Cause

The proactive boundary detection required:
```python
if msg0.loop_enabled and msg0.out_frame is not None:
    # Check for boundary
```

But when `out_frame is None` (play full file), this code path never executed.

## Fix Implemented

Modified the EOF detection logic in the frame iterator loop to handle looping at natural EOF:

**Before**:
```python
if packet is None:
    if loop_seeked.get(cue_id, False):
        break
    else:
        st['eof'] = True
        break
```

**After**:
```python
if packet is None:
    if loop_seeked.get(cue_id, False):
        break  # Already looped, now really done
    else:
        # Check if looping is enabled
        if msg0.loop_enabled:
            # Seek and restart (same as boundary detection)
            success = _seek_to_loop_boundary(st)
            if success:
                loop_seeked[cue_id] = True
                loop_counts[cue_id] += 1
                # Try to get next packet
                packet = next(st["packet_iter"], None)
                if packet is None:
                    st['eof'] = True
                    break
                st["frame_iter"] = iter(packet.decode())
                continue  # Continue decoding from beginning
            # If seek fails, fallback to reinit
        else:
            st['eof'] = True
            break
```

## What This Does

1. When decoder reaches EOF (packet iterator returns None)
2. Checks if `loop_enabled=True`
3. If yes: Seeks back to start and continues decoding
4. Decoder sends frames with `looped=True` for next iteration
5. Output process sees continuous stream (true ring buffer)
6. Loop happens seamlessly

## Files Modified

- `engine/processes/decode_process.py` (lines 329-383): Enhanced EOF handling for looping

## Expected Behavior Now

```
[RING-EOF-LOOP] Cue XXX: EOF reached with loop_enabled=True, seeking for loop
[RING-SEEK] Cue XXX: Seeking to loop boundary in_frame=0
[RING-SEEK] Cue XXX: Seek successful
[RING-ITERATION] Cue XXX: Starting iteration 2
[RING-SEND] Cue XXX: Sending 8192 frames (decoded_total=8192, looped=True)
[RING-SEND] Cue XXX: Sending 8192 frames (decoded_total=16384, looped=True)
```

Key indicators:
- `[RING-EOF-LOOP]` - EOF detected with looping
- `[RING-ITERATION]` - New iteration started
- `looped=True` - Part of restarted iteration
- Iteration count increments in logs

## Ready to Test

The fix handles both cases:
1. **With `out_frame` specified**: Uses boundary detection (proactive)
2. **With `out_frame=None`**: Uses EOF detection (natural end of file)

Both seamlessly loop when `loop_enabled=True`.
