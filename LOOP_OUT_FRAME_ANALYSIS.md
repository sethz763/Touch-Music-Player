# Loop Out-Frame Bug Analysis

## Summary of the Issue

Clips with custom out_points only loop a few times then stop. The problem is **NOT** in the loop restart frame calculation itself, but rather in how `decoded_frames` is managed through multiple loop iterations when an `out_frame` boundary exists.

## Key Files and Locations

### 1. **Pooled Decoder (Primary Implementation)**
File: [engine/processes/decode_process_pooled.py](engine/processes/decode_process_pooled.py)

#### Loop Restart Seek (Multi-threaded Job Handler)
**Lines 325-340:** Pool worker thread handles loop restart when reaching EOF with `loop_enabled=True`

```python
if packet is None:
    if job.cmd.loop_enabled:
        try:
            seek_ts = 0 if job.cmd.in_frame == 0 else int((job.cmd.in_frame / job.cmd.target_sample_rate) / job.stream.time_base)
            job.container.seek(seek_ts, stream=job.stream, any_frame=False, backward=True)
            job.packet_iter = job.container.demux(job.stream)
            job.loop_count += 1
            job.is_loop_restart = True
            # Reset per-iteration position so out_frame trimming behaves correctly.
            job.decoded_frames = 0  # <-- CRITICAL RESET
            job.discard_frames = job.cmd.target_sample_rate // 100 if job.cmd.in_frame > 0 else 0
```

#### Out-Frame Boundary Check (Lines 373-381)
**Where the boundary check happens for looping cues:**

```python
if job.cmd.out_frame is not None:
    # out_frame is treated as an absolute frame index in the source.
    # decoded_frames tracks frames produced since in_frame.
    remaining = int(job.cmd.out_frame) - (int(job.cmd.in_frame) + int(job.decoded_frames))
    if remaining <= 0:
        job.eof = True
        reached_target = True
        break
    if pcm.shape[0] > remaining:
        pcm = pcm[:remaining, :]  # <-- TRIM TO BOUNDARY
```

**Critical calculation:**
```
remaining = out_frame - (in_frame + decoded_frames)
```

This formula assumes `decoded_frames` is relative to `in_frame`. When looping:
- After each loop restart, `decoded_frames` is reset to 0
- As we decode from `in_frame` again, `decoded_frames` accumulates
- When we reach `out_frame`, we should have: `out_frame - in_frame = total_frames_per_loop`

---

### 2. **Single-threaded Decoder (Fallback Implementation)**
File: [engine/processes/decode_process_pooled.py](engine/processes/decode_process_pooled.py) - Lines 560-820

#### Loop Restart Seek (Single-threaded path)
**Lines 793-815:** Single-threaded loop restart handling

```python
if eof:
    if start_cmd.loop_enabled:
        try:
            # Seek back to in_frame
            if start_cmd.in_frame == 0:
                seek_ts = 0
            else:
                seek_ts = int(
                    (start_cmd.in_frame / start_cmd.target_sample_rate) / stream.time_base
                )
            container.seek(seek_ts, stream=stream, any_frame=False, backward=True)
            packet_iter = container.demux(stream)
            frame_iter = None
            # Reset per-iteration position so out_frame trimming behaves correctly.
            decoded_frames = 0  # <-- CRITICAL RESET
            discard_frames = (
                start_cmd.target_sample_rate // 100 if start_cmd.in_frame > 0 else 0
            )
            eof = False
            is_loop_restart = True
```

#### Out-Frame Boundary Check (Lines 734-741)
```python
if start_cmd.out_frame is not None:
    # out_frame is treated as an absolute frame index in the source.
    # decoded_frames tracks frames produced since in_frame.
    remaining = int(start_cmd.out_frame) - (int(start_cmd.in_frame) + int(decoded_frames))
    if remaining <= 0:
        eof = True
        reached_target = True
        break
    if pcm.shape[0] > remaining:
        pcm = pcm[:remaining, :]
```

---

## The Suspected Bug

### **Scenario: Clip with in_frame=1000, out_frame=3000, loop_enabled=True**

**Iteration 1 (First Loop):**
1. Start decoding from frame 1000
2. `decoded_frames = 0` initially
3. As we decode: `decoded_frames` increments from 0 → 2000 (to reach out_frame=3000)
4. Check: `remaining = 3000 - (1000 + 2000) = 0` → EOF + loop restart

**Iteration 2 (Loop Restart):**
1. Seek back to frame 1000
2. **Reset:** `decoded_frames = 0` ✓
3. Start decoding again
4. Should work correctly...

**Possible Issues to Investigate:**

1. **Is `decoded_frames` being reset correctly on every loop restart?**
   - Both implementations reset it, but is the reset happening BEFORE the next decode cycle?

2. **Is `discard_frames` being recalculated on loop restart?**
   - Should be: `discard_frames = sample_rate // 100 if in_frame > 0 else 0`
   - This discards ~10ms of frames to account for decoder seeking imprecision
   - If not reset properly, we might skip frames on iteration 2+

3. **Are pending PCM frames being carried over between loop iterations?**
   - `job.pending_pcm` or `pending_pcm` might retain samples from previous iteration
   - These could be miscounted against the next loop's `decoded_frames`

4. **Is the UpdateCueCommand properly updating `out_frame` during playback?**
   - Line 311 shows: `job.cmd.out_frame = pending_cmd.out_frame`
   - If an update arrives mid-loop, it could corrupt the boundary check

---

## Related Code Paths

### Audio Engine (Sends Loop Commands)
File: [engine/audio_engine.py](engine/audio_engine.py)

**Lines 465-473:** UpdateCueCommand is issued with new out_frame
```python
self._dbg_print(f"[AUDIO-ENGINE] Updating cue {cmd.cue_id}: in_frame={cmd.in_frame} out_frame={cmd.out_frame} gain_db={cmd.gain_db} loop_enabled={cmd.loop_enabled}")

if not is_update:
    # ... create cue
else:
    # Update existing cue
    cue = Cue(
        cue_id=cmd.cue_id,
        in_frame=cmd.in_frame,
        out_frame=cmd.out_frame,
```

### Output Process (Listens for Loop Events)
File: [engine/processes/output_process.py](engine/processes/output_process.py)

**Lines 1598-1610:** Loop restart handling
```python
if msg.is_loop_restart:
    _log(f"[START-CUE-LOOP-RESTART] cue={msg.cue_id[:8]}")
    # ... handle loop restart
```

---

## Debugging Steps

To confirm the bug, add logging at these points:

### In Pooled Worker (Lines 335-340):
```python
job.decoded_frames = 0
print(f"[LOOP-RESTART] cue={cue_id[:8]} loop_count={job.loop_count} "
      f"resetting decoded_frames=0 pending_pcm={job.pending_pcm.shape if job.pending_pcm is not None else None}")
```

### In Out-Frame Check (Lines 373-381):
```python
if job.cmd.out_frame is not None:
    remaining = int(job.cmd.out_frame) - (int(job.cmd.in_frame) + int(job.decoded_frames))
    print(f"[OUT-FRAME-CHECK] cue={cue_id[:8]} loop={job.loop_count} "
          f"out_frame={job.cmd.out_frame} in_frame={job.cmd.in_frame} "
          f"decoded_frames={job.decoded_frames} remaining={remaining}")
    if remaining <= 0:
```

### In Decode Output (Lines 412-424):
```python
job.decoded_frames += pcm.shape[0]
print(f"[DECODED-UPDATE] cue={cue_id[:8]} loop={job.loop_count} "
      f"added={pcm.shape[0]} total_decoded_frames={job.decoded_frames}")
```

---

## Data Structures

### DecodedChunk (Line 35-50)
```python
@dataclass
class DecodedChunk:
    cue_id: str
    track_id: str
    pcm: np.ndarray
    eof: bool = False
    is_loop_restart: bool = False
    decoder_produced_mono: float = 0.0
    decode_work_ms: float = 0.0
    worker_id: int = -1
```

### UpdateCueCommand (engine/commands.py, Lines 195-208)
```python
@dataclass
class UpdateCueCommand:
    """Update in_frame, out_frame, gain_db, or loop_enabled for a playing cue.
    
    - Loop boundary changes (in_frame, out_frame) take effect on next loop iteration.
    """
    cue_id: str
    in_frame: Optional[int] = None
    out_frame: Optional[int] = None
    gain_db: Optional[float] = None
    loop_enabled: Optional[bool] = None
```

---

## Related Commands and Structures

File: [engine/commands.py](engine/commands.py)

**DecodeStart (Lines 95-130):**
```python
@dataclass
class DecodeStart:
    """Start decoding a file. Replaces any existing decode for this cue_id."""
    cue_id: str
    track_id: str
    file_path: str
    in_frame: int = 0
    out_frame: Optional[int] = None  # Stop at this frame (None = end of file)
    target_sample_rate: int = 48000
    target_channels: int = 2
    loop_enabled: bool = False  # Loop from out_frame to in_frame if True.
```

---

## Summary of Loop-Related Code Locations

| Purpose | File | Lines | Key Variable |
|---------|------|-------|--------------|
| Loop restart seek (pool worker) | decode_process_pooled.py | 325-340 | `job.decoded_frames`, `job.discard_frames` |
| Out-frame boundary check (pool worker) | decode_process_pooled.py | 373-381 | `remaining = out_frame - (in_frame + decoded_frames)` |
| Frame accumulation (pool worker) | decode_process_pooled.py | 412-413 | `job.decoded_frames += pcm.shape[0]` |
| Loop restart seek (single-thread) | decode_process_pooled.py | 793-815 | `decoded_frames`, `discard_frames` |
| Out-frame boundary check (single-thread) | decode_process_pooled.py | 734-741 | `remaining = out_frame - (in_frame + decoded_frames)` |
| Frame accumulation (single-thread) | decode_process_pooled.py | 767-768 | `decoded_frames += pcm.shape[0]` |
| Loop restart event emission | audio_engine.py | 1182-1200 | `is_loop_restart=True` |
| Loop restart handling (output) | output_process.py | 1598-1610 | `is_loop_restart` flag |

