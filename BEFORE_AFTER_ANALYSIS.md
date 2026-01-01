# Before vs After: Ring Buffer Loop Architecture

## Problem Diagram

### BEFORE (Broken - Race Condition):

```
Timeline of Events:

T0: Decoder reaches EOF
    sends frames with eof=True
    |
    v

T1: Output callback consumes frames
    from ring
    |
    v

T2: Output detects eof=True
    and sets finished_pending=True
    |
    v

T3: Output main loop checks finished_pending
    Clears ring (q.clear(), frames=0, eof=False)
    |
    v
    
    [RACE CONDITION HERE]
    ↓
    
T3.5: Decoder just sent first frame of next iteration
      Ring.push() called from decode process
      BUT ring is in process of being cleared!
      Frames may be lost or corrupted
    
T4: Ring clear completes
    Now ready for new frames
    
T5: Output detects ring has frames again
    Starts feeding audio
    
❌ RESULT: First 4-8KB of next iteration lost
```

### Problem: Multi-Process Race
```
DECODE PROCESS                          OUTPUT PROCESS
└─ Sending frames                       └─ Clearing ring
   (async IPC queue)                       (thread-unsafe operations)
   
   Both writing to ring concurrently WITHOUT synchronization
   → Undefined behavior
   → Frame loss
   → Potential corruption
```

---

## AFTER (Fixed - True Ring Buffer):

```
Timeline of Events:

T0: Decoder detects approaching boundary
    (decoded_frames ~= out_frame)
    |
    v

T1: Decoder immediately seeks to start
    (BEFORE final frame, BEFORE EOF)
    packet_iter reset
    frame_iter reset
    decoded_frames = 0
    eof = False (stays False!)
    |
    v

T2: Decoder continues decoding from position 0
    Sends frames with eof=False
    Output never knows anything happened!
    |
    v

T3: Output callback consumes frames normally
    Ring is never set to eof for looping
    finished_pending is never triggered
    |
    v

T4: Loop iteration N+1 proceeds seamlessly
    No ring clearing
    No race condition
    No gap in audio
    
✅ RESULT: Seamless looping, zero frame loss
```

### Solution: Decoder Owns the Loop Boundary
```
DECODE PROCESS                          OUTPUT PROCESS
└─ Proactively seeks                   └─ Drains ring normally
   at boundary                            (unaware of loop)
   eof stays False for looping            
   continues from start                   
   
   Single process handles restart
   Output is a simple consumer
   → No coordination needed
   → No race conditions
   → No special logic
```

---

## Code Comparison

### OLD APPROACH: Reactive EOF-Based Restart

#### Decode Process:
```python
# When reaching EOF:
if st['eof'] and msg0.loop_enabled:
    # Try restart
    success = _restart_cue_decoder_fast(st)
    if success:
        # Send looped event
        event_q.put(("looped", cue_id, ...))
        # Mark to skip cleanup
        st["just_restarted"] = True
        # Pre-buffer one block
        st["credit_frames"] = msg0.block_frames
    else:
        # Fallback to full reinitialization
        ...
```

**Problems**:
- `just_restarted` flag (defensive)
- Pre-buffering logic adds complexity
- Restart happens AFTER EOF visible to output
- Multiple code paths (fast vs full)

#### Output Process:
```python
# Complex looping logic:
if ring.finished_pending:
    is_looping = cue_id in looping_cues
    if not is_looping:
        emit finish event
    else:
        # Special loop restart handling:
        ring.eof = False
        ring.frames = 0
        ring.q.clear()  # ← RACE CONDITION
        ...
```

**Problems**:
- Ring clearing happens after frames arrive (race)
- Two different paths (looping vs non-looping)
- Ring state coordination fragile

---

### NEW APPROACH: Proactive Boundary-Based Seeking

#### Decode Process:
```python
# Lookahead for boundary (frame-count based):
LOOKAHEAD_WINDOW = msg0.block_frames * 2
remaining_frames = msg0.out_frame - st["decoded_frames"]

if remaining_frames <= LOOKAHEAD_WINDOW and \
   not loop_seeked.get(cue_id, False):
    st["should_seek_for_loop"] = True

# When hitting boundary:
if st["decoded_frames"] >= msg0.out_frame and msg0.loop_enabled:
    if not loop_seeked.get(cue_id, False):
        success = _seek_to_loop_boundary(st)
        loop_seeked[cue_id] = True
        loop_counts[cue_id] += 1

# Send frames - NEVER eof for looping:
out_q.put(DecodedChunk(..., eof=False))
```

**Benefits**:
- Single code path (proactive seeking)
- No pre-buffering needed (implicit)
- Seek happens BEFORE EOF
- Loop boundary invisible to output

#### Output Process:
```python
# Simple finished handling (no loop special case):
if ring.finished_pending:
    event_q.put(("finished", cue_id))
    rings.pop(cue_id, None)
    # That's it - no clearing logic
```

**Benefits**:
- No special looping code
- No ring clearing needed
- Simpler to understand and maintain
- True ring buffer behavior

---

## State Machine Comparison

### OLD: Reactive States
```
┌─────────────────┐
│   Decoding      │
│ (credit > 0)    │
└────────┬────────┘
         │
         ▼ decoded_frames >= out_frame
┌─────────────────┐
│    At EOF       │
│  (eof=True)     │
└────────┬────────┘
         │
         ├─→ Loop enabled?
         │
         ├─ Yes: Seek
         │       (tries_restarted=True)
         │       ▼ next iteration
         │   Pre-buffer
         │
         └─ No: Finish
             (emit event)
```

**Issue**: Multiple transitions, flags, special states

### NEW: Proactive Lookahead
```
┌─────────────────┐
│   Decoding      │
│ (credit > 0)    │
└────────┬────────┘
         │
         ├─ decoded >= lookahead?
         │
         ├─ Yes: Seek in lookahead phase
         │       (before hitting exact boundary)
         │       (loop_seeked=True)
         │
         │       ▼ continue decoding
         │   (now from beginning)
         │
         └─ No: Keep decoding
             ▼
         (seamless continuation)
```

**Benefit**: Single forward-flowing state, no special cases

---

## Frame Flow Comparison

### OLD: With Gap Risk
```
ITERATION 1                         ITERATION 2
┌──────────────┐                   
│ Frame data   │                   
│ 0...N        │ ──────────┐
└──────────────┘           │
                    ┌──────▼──────┐
                    │ Ring Buffer │
                    │ (1-2 blocks) │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Callback  │
                    │   Consumes  │
                    └──────┬──────┘
                           │
                           ▼
                    [RING CLEARING]
                    frames=0, eof=False
                           │
                           │ ⚠️ GAP ⚠️
                           │ Frames arriving now get lost
                           │
                           ▼
                    ┌──────────────┐
                    │ New frames   │
                    │ (start lost) │
                    └──────────────┘

❌ First ~2000 frames of iteration 2 lost
```

### NEW: Seamless Flow
```
ITERATION 1                         ITERATION 2
┌──────────────┐                   
│ Frames 0..N  │                   
└──────────────┘                   
   │ (normal)                      
   ├─►Ring Buffer (4-8KB)◄─────┐
   │                              │
   ├─►Callback eats───────┐      │
   │                      │      │
   │                      ├─►Plays audio
   │                      │
   └─[SEEK WHILE PLAYING]─┘
       (lookahead window)
       decoded_frames=0
       eof=False (!)
       
   │ (continue from frame 0)
   ├─►Frames 0..M      ◄─────────┐
   │                              │
   ├─►Ring Buffer ◄──────────────┤
      (stays full)                │
                                  │
                    ┌─────────────┴──────┐
                    │   Seamless        │
                    │   No gap          │
                    │   No loss         │
                    └───────────────────┘

✅ All frames delivered, no loss
```

---

## Key Insight: The Paradigm Shift

| Aspect | OLD | NEW |
|--------|-----|-----|
| **Responsibility** | Output detects loop → restarts | Decoder detects boundary → loops |
| **Timing** | Reactive (after EOF) | Proactive (before EOF) |
| **Synchronization** | Implicit (flags + timing) | Explicit (frame count) |
| **Ring Role** | Restored per loop | Continuous stream |
| **Output Logic** | Special cases | Generic |
| **Race Condition** | Buffer state during restart | None (internal to decoder) |

**The shift**: From "output-driven loop management with inter-process coordination" to "decoder-owned loop boundary with simple output consumer"

This is a fundamental architectural improvement that makes looping **simpler, safer, and more reliable**.
