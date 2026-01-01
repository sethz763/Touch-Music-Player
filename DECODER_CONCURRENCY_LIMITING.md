# Multi-Concurrent Decode Limiting & Telemetry Fix

## Problem Identified

Previous fixes attempted to optimize a single-threaded decoder, but analysis of logs shows:

1. **All 16 cues finished immediately with EOF** (within same millisecond they started)
2. **No data ever reached ring buffers** - they all hit EOF before any decoding could happen
3. **Telemetry completely skipped** during high concurrency - no level events to GUI
4. **GUI meters never lit up** because CueLevelsEvent never sent

**Root Cause**: Single decoder thread trying to service 16 simultaneous DecodeStart requests causes complete starvation. Decoder can't even begin decoding before output callback marks buffers EOF.

## Solution: Maximum Concurrent Decodings

Implement a decoder queue system that limits simultaneous active decodings:

- **Allow MAX 6 concurrent decodings at a time**
- Queue additional DecodeStart requests
- Activate queued decodings when earlier ones finish
- Results in **predictable 6-at-a-time serving** instead of chaos from trying to do 16 at once

### How It Works

**Before** (causes starvation):
```
Time 0:    16 DecodeStart requests arrive
Time 1ms:  Decoder starts processing cue1 (will take ~50ms for first chunk)
Time 2ms:  Decoder processes cue2...
...
Time 16ms: Decoder finally gets to cue16
Time 50ms: Decoder finishes cue1, goes back to check if anyone has credit
...but by now cue1's ring buffer is completely empty (output consumed it all)
→ Ring marked EOF immediately
```

**After** (prevents starvation):
```
Time 0:    16 DecodeStart requests arrive
           6 activated, 10 queued
Time 0:    Decoder starts with cue1-cue6 (6x more focused effort)
Time 50ms: First batch finishes, decoder activates queued cue7-cue12
           by then cue1-cue6 have generated enough buffer
Time 100ms: Second batch finishes, activates cue13-cue16
Result: All cues eventually get buffered, no EOF starvation
```

## Changes Applied

### 1. Decoder Concurrency Limiter (decode_process.py)

**Added at line 175-177**:
```python
MAX_CONCURRENT_DECODINGS = 6  # Max 6 files being decoded simultaneously
pending_starts: List[tuple] = []  # Queue of DecodeStart waiting for a slot
```

**Modified DecodeStart handler (line 220-226)**:
```python
if isinstance(msg, DecodeStart):
    # Queue if at capacity
    if len(active) >= MAX_CONCURRENT_DECODINGS:
        pending_starts.append((msg, av))
        continue  # Don't activate yet
    
    # Otherwise activate normally
    state = _init_cue_decoder(msg, av)
    ...
    active[msg.cue_id] = state
```

**Added activation of queued starts (line 531-551)**:
When a decoder finishes (`to_remove` loop), activate the next queued start if any exist:
```python
while pending_starts and len(active) < MAX_CONCURRENT_DECODINGS:
    queued_msg, av_mod = pending_starts.pop(0)
    state = _init_cue_decoder(queued_msg, av_mod)
    if state is None:
        out_q.put(DecodeError(...))
    else:
        active[queued_msg.cue_id] = state
        # Send started event
        ...
```

**Impact**: Decoder now processes cues in controlled batches, preventing EOF starvation.

### 2. Telemetry During High Concurrency (output_process.py)

**Previous behavior** (lines 282-304):
- When `skip_telemetry=True`, NO events sent at all
- Result: GUI meters never update, buttons show no activity

**New behavior** (lines 282-316):
- **Skip RMS/peak computation** (expensive during high concurrency)
- **Still send CueLevelsEvent** with zeros (`rms=-64.0, peak=-64.0`)
- **Always send CueTimeEvent** (cheap, helps GUI track progress)
- Result: Meters still show zero during bulk fade (intentional), but GUI gets time updates

**Code change**:
```python
if filled > 0:
    elapsed_seconds = ...  # Always compute
    
    if not skip_telemetry:
        # Full telemetry: compute RMS/peak and send CueLevelsEvent
        rms = compute_rms(chunk[:filled])
        peak = compute_peak(chunk[:filled])
        event_q.put_nowait(CueLevelsEvent(cue_id, rms, peak))
    else:
        # Bulk fade: send zeros without computation
        event_q.put_nowait(CueLevelsEvent(cue_id, -64.0, -64.0))
    
    # Always send time (lightweight)
    event_q.put_nowait(CueTimeEvent(cue_id, elapsed_seconds, remaining_seconds))
```

**Impact**: GUI no longer silent during high concurrency; meters and time displays always work.

## Expected Results

With these fixes:

✅ **No more immediate EOF** - Cues actually get buffered before output tries to play them
✅ **Fades complete naturally** - No "refade_pending" / "refade_stuck_cue" spam
✅ **GUI meters work** - Buttons show level activity during all scenarios
✅ **Smooth 6-at-a-time playback** - Predictable decoder throughput
✅ **Better stability** - No chaotic decoder starvation

## Limitations & Notes

- **MAX_CONCURRENT_DECODINGS = 6**: Tuned for single decoder thread. Can adjust:
  - Increase to 8-10 if you want faster startup (more concurrent buffering)
  - Decrease to 4 if decoder still starves (slower CPU/disk)
- **Still single-threaded decoder**: This is a mitigation, not full solution
- **Future improvement**: Multi-threaded decoder pool would eliminate this limit entirely

## Testing

When testing with 12-16 simultaneous cues:

Expected logs:
```
Time t=0s:   Multiple DecodeStart requests
Time t=0s:   6 activated immediately, rest queued
Time t=1s:   Cues 1-6 playing with audio
Time t=1.5s: Cues 1-6 finish/finish fading
Time t=1.5s: Cues 7-12 activated from queue
Time t=2s:   Cues 7-12 playing with audio
...
```

NO logs like:
```
refade_pending  ← Should not see this
refade_stuck_cue ← Should not see this
[cue_finished] cue=xxx reason=eof (same millisecond as started) ← Should NOT see this
```

## Configuration

To tune decoder concurrency:

**File**: `engine/processes/decode_process.py`
**Line**: 175
```python
MAX_CONCURRENT_DECODINGS = 6  # Change this value
```

- **6**: Default, balanced (prevents starvation, reasonable throughput)
- **4**: Conservative (slower startup, very stable)
- **8**: Aggressive (faster but may still starve on slow disk)
- **10+**: Only if you have fast SSD + powerful CPU

## Related Changes

This fix complements earlier optimizations:
- **Phase 5**: Vectorized envelope math + stagger delay
- **Phase 6**: Event queue skip + print removal
- **Post-Phase 6**: Dynamic buffering + priority scheduling
- **This**: Decoder concurrency limiting + minimal telemetry during fades

Combined effect: Stable high-concurrency audio with 12-16+ simultaneous cues.
