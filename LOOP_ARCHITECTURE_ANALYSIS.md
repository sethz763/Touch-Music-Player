# Audio Loop Architecture Analysis & Proposed Ring Buffer Solution

## Current Problem: Frame Loss During Loop Restart

### Root Causes Identified

1. **Race Condition Between Processes**
   - Decode process restarts and begins sending frames
   - Output process hasn't cleared its ring yet (async, happens later)
   - Frames arrive and get buffered **before** ring is cleared
   - Ring clear then discards those frames
   - **Result**: First 4-8KB of looped audio lost

2. **Defensive EOF Handling**
   - Multiple places check for EOF and may suppress frames
   - Ring gets pre-cleared in anticipation of loop restart
   - Decoder pre-buffers but ring isn't ready
   - Complex state coordination between "just_restarted" flags

3. **Seek Tolerance Discard**
   - 50ms of frames discarded after seek as tolerance
   - But exact amount varies due to resampler state
   - Inconsistent discard amounts per iteration

4. **Output Process Ring Clearing**
   - Ring cleared when `finished_pending=True` (after audio consumed)
   - But decode may have already sent next iteration frames
   - Pre-buffered frames get discarded in the clear

---

## Proposed Solution: True Ring Buffer with Proactive Restart

### Key Insight
Make the **decoder** responsible for the loop boundary, not the output process.
- Decoder anticipates the boundary BEFORE reaching EOF
- When approaching end (based on frame counts), decoder:
  - Completes current iteration
  - Immediately seeks to beginning
  - Pre-decodes first packet
  - Output doesn't even know a restart happened

### Architecture Change

```
CURRENT (Broken):
┌─────────────────────────────────────────────────────────────┐
│ Decode Process                                               │
│ - Decodes frames 0..N                                        │
│ - Reaches EOF, marks eof=True                                │
└─────────────────────────────────────────────────────────────┘
                            ↓
         ┌──────────────────────────────────────────────────┐
         │ Output Process                                    │
         │ - Consumes frames                                │
         │ - Detects ring.finished_pending=True             │
         │ - Clears ring (❌ RACE: may discard pre-buffered) │
         │ - Requests more frames                           │
         └──────────────────────────────────────────────────┘
                            ↓
                  ┌─────────────────────┐
                  │ Decode receives req. │
                  │ Seeks to start       │
                  │ Sends frame 0        │
                  └─────────────────────┘
                  ⚠️ Gap here - frames lost


NEW (Ring Buffer):
┌──────────────────────────────────────────────────────────────┐
│ Decode Process                                                │
│ - Tracks decoded_frames vs out_frame                          │
│ - When decoded_frames ~= out_frame (near end):               │
│   1. Finish current iteration (send remaining frames)        │
│   2. Mark eof=True (but decoder ALREADY sought to start)    │
│   3. Pre-decoded first frame ready (hidden buffering)        │
│   4. Suppress EOF to output (loop_enabled=True)             │
│   5. Continue seamlessly - output never knew                │
└──────────────────────────────────────────────────────────────┘
                            ↓
         ┌──────────────────────────────────────────────────┐
         │ Output Process                                    │
         │ - Consumes frames from ring                       │
         │ - Ring stays full (no stutter)                   │
         │ - Decoder already looped internally               │
         │ - No race conditions                              │
         └──────────────────────────────────────────────────┘
         ✅ Seamless, no gaps, true ring behavior
```

### Implementation Details

#### Decoder Side Changes

```python
def decode_process_main(...):
    # Per-cue state additions:
    # - lookahead_buffer: [frames_to_buffer, next_packets_decoded]
    # - will_loop_next: bool  # flag that next iteration is prepped
    
    while st["credit_frames"] > 0:
        # ... normal frame decoding loop ...
        
        # NEW: Check if we're NEAR the boundary (not AT it yet)
        remaining_to_boundary = msg0.out_frame - st["decoded_frames"]
        
        if remaining_to_boundary <= LOOKAHEAD_WINDOW and msg0.loop_enabled:
            if not st.get("will_loop_next"):
                # We're close to the boundary
                # After sending current frames, we'll seek back
                st["will_loop_next"] = True
                print(f"[RING-PREP] Cue {cue_id}: Approaching boundary, will loop next iteration")
        
        # Existing logic, then:
        
        if st["decoded_frames"] >= msg0.out_frame and msg0.out_frame is not None:
            # We've hit the boundary
            if msg0.loop_enabled and st.get("will_loop_next"):
                # Immediately seek back (before returning to output)
                success = _restart_cue_decoder_fast(st)
                if success:
                    # Mark EOF so output knows, but we're already at start
                    st['eof'] = True
                    st['will_loop_next'] = False
                    # Continue to next iteration without output noticing
```

#### Output Side Simplification

Instead of complex ring clearing logic:

```python
# SIMPLER: Trust that decoder handles restart internally
for cue_id, ring in list(rings.items()):
    if ring.finished_pending:
        if cue_id not in looping_cues:
            # Not looping - truly finished
            event_q.put(("finished", cue_id))
            rings.pop(cue_id, None)
        else:
            # Looping - decoder already restarted
            # Just wait for more frames, they're coming
            ring.finished_pending = False
            # DON'T clear ring - decoder is flowing smoothly
```

---

## Benefits

1. **No Race Conditions**: Decoder owns the restart, output is just a consumer
2. **No Frame Loss**: Seek happens BEFORE EOF is visible to output
3. **Smoother Loop**: Output sees continuous PCM flow (true ring buffer behavior)
4. **Simpler Logic**: Less state coordination between processes
5. **Natural Backpressure**: Buffer requests work as intended
6. **Scalable**: Works with multiple looping cues simultaneously

---

## Frame Loss Prevention

**Current approach**: Hope ring is clear before new frames arrive → **Race condition**

**New approach**: 
- Decoder knows when it's at/near boundary
- Seek + pre-buffer **before** sending that final `eof=True` chunk
- Output receives continuous flow
- Loop restart is internal to decoder
- Output process doesn't need to do anything special

---

## Migration Path

1. Add `lookahead_window` and `will_loop_next` state to decoder
2. Modify boundary detection to use lookahead (frame count based, deterministic)
3. On lookahead trigger: immediately seek to start and decode one block
4. Suppress internal EOF until output has consumed all frames
5. Remove `just_restarted` and complex restart flags
6. Let output process handle normal ring clearing (non-looping case)
7. For looping: output sees no EOF, keeps pulling frames naturally

---

## Testing Strategy

1. **Frame Count Validation**: Track frame count per iteration
   - Should be identical across all iterations
   - Discard amount should be consistent

2. **Ring Buffer State**: Verify ring never has "leftover" frames
   - Before: would have pre-buffered frames → clear drops them
   - After: ring smoothly flows without empties

3. **Timing**: Loop restart should be seamless
   - No gap between iterations
   - No silence between loops
   - Callback gets continuous data

4. **Multi-Cue**: Multiple simultaneous loops should work
   - Each maintains independent state
   - No crosstalk
