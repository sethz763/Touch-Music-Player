# Ring Buffer Architecture: Code Reference

## New Functions

### `_seek_to_loop_boundary(st: dict) -> bool`

**Location**: `engine/processes/decode_process.py`

**Purpose**: Proactively seek to the loop start position when approaching the loop boundary

**Parameters**:
- `st`: Decoder state dictionary for the cue

**Returns**: 
- `True` if seek successful
- `False` if seek failed

**What it does**:
1. Calculates seek position from `msg.in_frame`
2. Calls container.seek()
3. Flushes codec contexts
4. Resets decoder state:
   - `decoded_frames = 0`
   - `eof = False`
   - `frame_iter = None`
   - `discard_after_seek = 2400` (50ms tolerance)
   - Recreates packet iterator
   - Recreates resampler
5. Returns success/failure

**When called**: When we detect boundary is about to be crossed

**Error handling**: Returns False if seek fails, caller handles fallback

---

## Modified State Variables

### New: `loop_seeked` Dictionary

**Type**: `Dict[str, bool]`

**Purpose**: Track which looping cues have proactively seeked

**Values**:
- `loop_seeked[cue_id]` not set or `False`: Haven't seeked yet, decoding normally
- `loop_seeked[cue_id] = True`: Have seeked, now decoding next iteration

**Lifecycle**:
1. Created as empty dict at process start
2. Set to `True` when seek executed
3. Removed when cue is cleaned up

**Usage in loop**:
```python
if not loop_seeked.get(cue_id, False) and remaining_frames <= LOOKAHEAD_WINDOW:
    # We're approaching boundary, seek coming next
    
if st["decoded_frames"] >= msg0.out_frame and not loop_seeked.get(cue_id, False):
    # Hit boundary, do seek now
    _seek_to_loop_boundary(st)
    loop_seeked[cue_id] = True

if loop_seeked.get(cue_id, False):
    # Already seeked, continue decoding from new position
```

---

## Modified State Dictionary (`st`)

### New Keys (in addition to existing):

#### `st["should_seek_for_loop"]` (optional)
- **Type**: `bool`
- **Purpose**: Mark that we should seek after current block
- **Set by**: Lookahead logic
- **Read by**: Boundary checking logic
- **Cleared**: After seek executed

#### `st["discard_after_seek"]` (already existed, enhanced)
- **Type**: `int`
- **Purpose**: Number of frames to discard after seeking (tolerance)
- **Value**: `target_sample_rate // 20` (50ms)
- **Reset**: Every time we seek

---

## Main Loop Changes

### OLD Logic (Removed):
```python
if st["eof"] and msg0.loop_enabled:
    # React to EOF
    success = _restart_cue_decoder_fast(st)
    if success:
        st["just_restarted"] = True
        st["credit_frames"] = msg0.block_frames
```

### NEW Logic (Replaces):
```python
# LOOKAHEAD PHASE
LOOKAHEAD_WINDOW = msg0.block_frames * 2
if msg0.loop_enabled and msg0.out_frame is not None:
    remaining = msg0.out_frame - st["decoded_frames"]
    if remaining <= LOOKAHEAD_WINDOW and not loop_seeked.get(cue_id, False):
        print(f"[RING-PROACTIVE] approaching boundary")

# BOUNDARY PHASE  
if st["decoded_frames"] >= msg0.out_frame and msg0.loop_enabled:
    if not loop_seeked.get(cue_id, False):
        success = _seek_to_loop_boundary(st)
        loop_seeked[cue_id] = True
        loop_counts[cue_id] += 1
```

---

## Frame Sending Changes

### OLD:
```python
# Send with eof flag
eof = False
if boundary_reached:
    eof = True
out_q.put(DecodedChunk(..., eof=eof))
```

### NEW:
```python
# Looping cues NEVER send eof=True
out_q.put(DecodedChunk(..., eof=False))
```

---

## Output Process Changes

### OLD Ring Clear Logic (Removed):
```python
if ring.finished_pending:
    is_looping = cue_id in looping_cues
    if is_looping:
        # Special loop case
        ring.eof = False
        ring.frames = 0
        ring.q.clear()  # ← race condition here
```

### NEW Simplified Logic:
```python
if ring.finished_pending:
    # Simple - just emit finish for all
    # Ring clearing only for non-looping
    event_q.put(("finished", cue_id))
    rings.pop(cue_id, None)
```

---

## Event and Logging

### Events Emitted

#### "looped" (optional, no longer required)
```python
event_q.put(("looped", cue_id, msg0.track_id, msg0.file_path))
```
- Still sent for tracking/UI purposes
- Not required for looping to work
- Decoder internal operation, not output-driven

### Log Prefixes

#### `[RING-PROACTIVE]`
Lookahead logic detected boundary approaching
```
[RING-PROACTIVE] Cue X: Approaching boundary (N frames left), will seek after current block
```

#### `[RING-BOUNDARY]`
Exact boundary reached, executing seek
```
[RING-BOUNDARY] Cue X: Hit boundary, seeking for loop
```

#### `[RING-SEEK]`
Seek operation executing
```
[RING-SEEK] Cue X: Seeking to loop boundary in_frame=Y
[RING-SEEK] Cue X: Seek successful
```

#### `[RING-ITERATION]`
New iteration starting (after seek)
```
[RING-ITERATION] Cue X: Starting iteration 2
```

#### `[RING-SEND]`
Frames sent to output
```
[RING-SEND] Cue X: Sending 8192 frames (decoded_total=16384, looped=True)
```

#### `[RING-DISCARD]`
Post-seek tolerance frames discarded
```
[RING-DISCARD] Cue X: Discarding 2400 frames (tolerance)
```

#### `[RING-ERROR]`
Error during decoding
```
[RING-ERROR] Cue X: Exception during decode: [details]
```

#### `[RING-REMOVE]`
Cue cleanup
```
[RING-REMOVE] Cue X: Removing (loop_iterations=3)
```

---

## Data Flow Diagram

```
decode_process_main()
│
├─ Command processing
│  ├─ DecodeStart → _init_cue_decoder() → active[cue_id]
│  ├─ DecodeStop → st["stopping"] = True
│  └─ BufferRequest → st["credit_frames"] += frames_needed
│
├─ Main loop (for each active cue)
│  │
│  ├─ Check stopping
│  │
│  ├─ Looping logic (NEW)
│  │  ├─ Calculate remaining_frames
│  │  ├─ If approaching boundary → lookahead
│  │  ├─ If at boundary → _seek_to_loop_boundary()
│  │  └─ Set loop_seeked[cue_id] = True
│  │
│  ├─ Decoding loop (while credit_frames > 0)
│  │  ├─ Get next packet
│  │  ├─ Decode frames
│  │  ├─ Resample
│  │  ├─ Discard post-seek tolerance
│  │  ├─ Trim to boundary if needed
│  │  └─ Accumulate in chunks[]
│  │
│  ├─ Send frames
│  │  └─ out_q.put(DecodedChunk(..., eof=False))  ← Never True for looping
│  │
│  └─ Cleanup stale cues
│
└─ Next iteration
```

---

## State Transitions

### Looping Cue Timeline

```
T=0   DecodeStart received
      ↓
      _init_cue_decoder()
      active[cue_id] = state
      
      Decoding starts
      Loop counts: decoded_frames=0, loop_count=0
      ↓
      
T=1   Lookahead triggered
      remaining_frames ≤ LOOKAHEAD_WINDOW
      [RING-PROACTIVE] log
      ↓
      
T=2   Boundary reached
      decoded_frames ≥ out_frame
      [RING-BOUNDARY] log
      ↓
      
T=2.1 Seek executed
      _seek_to_loop_boundary(st)
      [RING-SEEK] log
      loop_seeked[cue_id] = True
      loop_counts[cue_id] = 1
      ↓
      
T=2.2 New iteration starts
      Continue decoding from decoded_frames=0
      [RING-ITERATION] log
      ↓
      
T=3   Normal frame transmission
      [RING-SEND] log (with looped=True)
      ↓
      
T=4   Boundary again
      Back to "Boundary reached" phase
      [RING-BOUNDARY] log
      loop_counts[cue_id] = 2
      ↓
      ...repeat...
      
T=N   Stop received
      st["stopping"] = True
      ↓
      Next iteration sends EOF
      Cue removed
```

---

## Key Constants

### LOOKAHEAD_WINDOW
```python
LOOKAHEAD_WINDOW = msg0.block_frames * 2
```
- **Default**: Usually 2048 frames (if block_frames=1024)
- **Purpose**: How far in advance to seek
- **Tuning**: 
  - Too small: Seeks too close to boundary, may hear artifacts
  - Too large: Wastes buffer space, less responsive
  - Safe range: 2-8 * block_frames

### Post-Seek Discard
```python
discard_after_seek = msg.target_sample_rate // 20  # 50ms
```
- **Purpose**: Seek tolerance (codecs may not seek exactly)
- **Value**: Fixed at 50ms (2400 frames @ 48kHz)
- **Tuning**: Could be adjusted per file if needed

---

## Integration Points

### For `audio_engine.py`
- No changes needed
- Loop restart is transparent
- Still listen to events if desired

### For `audio_service.py`
- May notice fewer "looped" events
- No special handling needed

### For UI/GUI
- Looping just works
- Can track iteration count via events or position tracking
- No forced updates needed

---

## Performance Characteristics

### Seek Operation (per loop)
- **Time**: ~5-10ms (varies by codec)
- **CPU**: Moderate spike during seek
- **I/O**: One seek operation per boundary

### Ring Buffer
- **Size**: Unchanged (typically 32-64KB)
- **Operations**: Slightly fewer (no explicit clear for loops)
- **Thread safety**: Improved (no inter-process state coordination)

### Overall
- **Latency**: Slightly lower (no EOF→clear lag)
- **Throughput**: Unchanged
- **Reliability**: Much higher (no race conditions)

---

## Error Cases and Handling

### Seek Failure
```python
success = _seek_to_loop_boundary(st)
if not success:
    # Fallback: Full reinitialization
    new_state = _init_cue_decoder(msg0, av)
    if new_state is None:
        # Both failed - emit error, remove cue
        out_q.put(DecodeError(...))
```

### No Packet After Seek
- Handled gracefully
- Frame iterator returns None
- Normal EOF path taken

### Malformed/Unseekable Files
- Seek fails → Fallback to reinit
- If reinit also fails → Error event
- Cue removed cleanly

---

## Testing Hooks

### Frame Tracking
```python
# In main loop, per chunk:
frames_in = pcm.shape[0]
total_frames += frames_in
iteration_frames += frames_in

print(f"[FRAME-COUNT] Cue {cue_id}: "
      f"iteration={loop_counts[cue_id]}, "
      f"this_iteration={iteration_frames}, "
      f"total={total_frames}")
```

### Seek Timing
```python
# Before seek:
t0 = time.time()

success = _seek_to_loop_boundary(st)

# After seek:
elapsed = time.time() - t0
print(f"[SEEK-TIME] Cue {cue_id}: {elapsed*1000:.1f}ms")
```

### Ring State (output process)
```python
# After each callback:
for cue_id, ring in rings.items():
    print(f"[RING-STATE] Cue {cue_id}: "
          f"frames={ring.frames}, "
          f"chunks={len(ring.q)}, "
          f"eof={ring.eof}")
```

---

## Summary

The new ring buffer architecture is built on these principles:

1. **Proactive Seeking**: Before boundary, not after
2. **Single Responsibility**: Decoder handles loop, output just plays
3. **Deterministic Timing**: Frame count based, not event based
4. **No Race Conditions**: All loop logic in one process
5. **Simpler Code**: Less state, fewer paths, clearer intent

All design choices follow from these principles.
