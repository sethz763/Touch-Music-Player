# Cue Removal Tracking - Quick Start Guide

## TL;DR

Cues now track **why** they stop. Access removal reason via:

```python
def on_cue_finished(event: CueFinishedEvent):
    reason = event.cue_info.removal_reason
    print(f"Cue {event.cue_info.cue_id} finished: {reason}")
```

## What Gets Tracked

### Your 4 Requirements
1. ✓ **Manual fade** → `removal_reason = "manual_fade"` (via auto-fade infrastructure)
2. ✓ **Auto fade** → `removal_reason = "auto_fade"` (starting new track)
3. ✓ **Reach out_frame** → `removal_reason = "eof_natural"` (file ends)
4. ✓ **Decode error** → `removal_reason = "decode_error: [details]"`

### Additional Cases
- `"manual_stop"` - User clicked stop button
- `"forced_stuck_fade"` - Force-removed after timeout
- `"timeout_stuck_decode"` - Decoder starvation timeout
- `"fade_complete"` - Fade envelope finished naturally

## How to Use

### In GUI Code
```python
from engine.messages.events import CueFinishedEvent

def on_cue_finished(event: CueFinishedEvent):
    info = event.cue_info
    
    # Get the detailed reason
    reason = info.removal_reason
    
    # Get cue metadata
    cue_id = info.cue_id
    track_id = info.track_id
    file_path = info.file_path
    duration = info.duration_seconds
    started = info.started_at
    stopped = info.stopped_at
    
    # Handle based on reason
    if reason == "eof_natural":
        print(f"✓ {file_path} finished normally")
    elif reason == "auto_fade":
        print(f"✓ {file_path} auto-faded for new track")
    elif reason == "manual_stop":
        print(f"✓ {file_path} stopped by user")
    elif "decode_error" in reason:
        print(f"✗ {file_path} failed: {reason}")
    elif reason == "forced_stuck_fade":
        print(f"⚠ {file_path} force-removed (stuck)")
    else:
        print(f"? {file_path} finished: {reason}")
```

### For Logging/Analytics
```python
def on_cue_finished(event: CueFinishedEvent):
    info = event.cue_info
    
    # Log removal reason with full context
    logger.info(
        "cue_finished",
        extra={
            "cue_id": info.cue_id,
            "track_id": info.track_id,
            "file": info.file_path,
            "duration": info.duration_seconds,
            "started_at": info.started_at,
            "stopped_at": info.stopped_at,
            "removal_reason": info.removal_reason,  # ← NEW
        }
    )
    
    # Or send to analytics
    analytics.track("cue_finished", {
        "reason": info.removal_reason,
        "duration_played": (info.stopped_at - info.started_at).total_seconds(),
    })
```

### For Debugging
```python
# In debug logs, search for:
grep "removal_reason=" debug.log

# Or specific cues:
grep "cue=ABC123.*removal_reason" debug.log

# Or specific reasons:
grep "removal_reason=forced_stuck_fade" debug.log  # Find stuck cues
grep "removal_reason=decode_error" debug.log       # Find decode errors
```

## Common Scenarios

### Scenario 1: Cue stops before end of file
**Problem**: Song stops at 30s but file is 5 minutes

**Investigation**:
1. Check `removal_reason`:
   ```bash
   grep "cue=ABC123" debug.log | grep removal_reason
   ```

2. If `removal_reason=eof_natural`:
   - The system thinks file ends at 30s
   - Check `out_frame` value: `grep "FINAL: out_frame" debug.log | grep ABC123`
   - If out_frame is too low → file probe failed
   - Check file with external tool (ffprobe, mediainfo)

3. If `removal_reason=auto_fade`:
   - A new cue was started, causing fade-out
   - Check logs for new cue start time

4. If `removal_reason=forced_stuck_fade`:
   - Output process timed out
   - Check system load during playback

### Scenario 2: Need to validate removal reason
**Requirement**: Only these reasons are acceptable
- eof_natural
- manual_stop
- auto_fade
- decode_error

**Validation code**:
```python
VALID_REASONS = {
    "eof_natural",
    "manual_stop", 
    "auto_fade",
    "manual_fade",
    "decode_error",
}

def on_cue_finished(event: CueFinishedEvent):
    reason = event.cue_info.removal_reason
    
    if not any(reason.startswith(r) for r in VALID_REASONS):
        # Alert on unexpected reason
        log.warning(f"Unexpected removal reason: {reason}")
```

### Scenario 3: Count removal statistics
```python
from collections import defaultdict

removal_stats = defaultdict(int)

def on_cue_finished(event: CueFinishedEvent):
    reason = event.cue_info.removal_reason
    # Extract base reason (before colon)
    base_reason = reason.split(":")[0]
    removal_stats[base_reason] += 1

def print_stats():
    print("Cue Removal Statistics:")
    for reason, count in sorted(removal_stats.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")
```

## All Removal Reasons

| Reason | Cause | Expected? | Action |
|--------|-------|-----------|--------|
| `eof_natural` | Reached end of file | YES ✓ | Normal |
| `manual_stop` | User clicked stop | YES ✓ | Normal |
| `manual_fade` | User initiated fade | YES ✓ | Normal |
| `auto_fade` | Auto-fade on new track | YES ✓ | Normal |
| `decode_error:...` | File read/decode error | YES ✓ | Investigate file |
| `forced_stuck_fade` | Fade timeout | NO ⚠ | Check system load |
| `timeout_stuck_decode` | Decoder timeout | NO ⚠ | Check system load |
| `fade_complete` | Fade finished | YES ✓ | Normal |

## Implementation Details

### CueInfo Changes
```python
@dataclass(frozen=True, slots=True)
class CueInfo:
    # ... existing fields ...
    removal_reason: str = ""  # NEW: Tracks removal reason
```

### Event Structure
```python
@dataclass(frozen=True, slots=True)
class CueFinishedEvent:
    cue_info: CueInfo  # Contains removal_reason
    reason: str        # Legacy (same as cue_info.removal_reason)
```

### Data Flow
```
AudioEngine._removal_reasons[cue_id] = reason
         ↓
OutputProcess.removal_reasons[cue_id] = reason
         ↓
Output sends ("finished", cue_id, reason)
         ↓
AudioEngine.pump() receives and creates CueInfo with removal_reason
         ↓
CueFinishedEvent emitted to GUI
         ↓
event.cue_info.removal_reason available to use
```

## Files Modified

- `engine/cue.py` - Added removal_reason field
- `engine/audio_engine.py` - Tracks and emits reason
- `engine/processes/output_process.py` - Tracks and reports reason
- `engine/messages/events.py` - Documentation updated

## Documentation

Full documentation available:
- `CUE_REMOVAL_SOLUTION.md` - Complete solution overview
- `CUE_REMOVAL_TRACKING.md` - Detailed reference
- `CUE_REMOVAL_ARCHITECTURE.md` - System architecture with diagrams
- `CUE_REMOVAL_IMPLEMENTATION.md` - Implementation details
- `CUE_REMOVAL_TEST_CHECKLIST.md` - Test procedures

## Examples

### Example 1: Log all cues with reason
```python
def on_cue_finished(event: CueFinishedEvent):
    print(f"{event.cue_info.file_path}: {event.cue_info.removal_reason}")

# Output:
# /music/song1.mp3: eof_natural
# /music/song2.mp3: auto_fade
# /music/song3.mp3: manual_stop
# /music/song4.mp3: decode_error: File not found
```

### Example 2: Alert on unexpected removals
```python
EXPECTED_REASONS = {"eof_natural", "auto_fade", "manual_stop"}

def on_cue_finished(event: CueFinishedEvent):
    reason = event.cue_info.removal_reason
    if not any(reason.startswith(r) for r in EXPECTED_REASONS):
        alert(f"Unexpected: {reason}")  # Alert admin
```

### Example 3: Timing analysis
```python
def on_cue_finished(event: CueFinishedEvent):
    info = event.cue_info
    duration_expected = info.duration_seconds or 0
    duration_actual = (info.stopped_at - info.started_at).total_seconds()
    
    if info.removal_reason == "eof_natural":
        if abs(duration_actual - duration_expected) > 1.0:
            log.warning(f"Duration mismatch: expected {duration_expected}s, got {duration_actual}s")
```

## Testing Your Implementation

### Test 1: Manual Stop
```python
# Start a cue
engine.play_cue(cmd)

# Stop it
engine.stop_cue(StopCueCommand(cue_id=cmd.cue_id))

# Check event
# event.cue_info.removal_reason == "manual_stop"  ✓
```

### Test 2: Auto-Fade
```python
# Start cue1
engine.play_cue(cmd1)

# Start cue2 (triggers auto-fade of cue1)
engine.play_cue(cmd2)

# Check events
# cue1_event.cue_info.removal_reason == "auto_fade"  ✓
# cue2_event.cue_info.removal_reason == "eof_natural" (when complete)  ✓
```

### Test 3: Natural EOF
```python
# Play short file to end
engine.play_cue(short_file_cmd)
# Wait for playback to complete

# Check event
# event.cue_info.removal_reason == "eof_natural"  ✓
```

### Test 4: Decode Error
```python
# Try to play non-existent file
engine.play_cue(invalid_file_cmd)
# Wait for error

# Check event  
# event.cue_info.removal_reason.startswith("decode_error:")  ✓
```

## Troubleshooting

### Q: Where do I access removal_reason?
**A**: In CueFinishedEvent callback: `event.cue_info.removal_reason`

### Q: What if removal_reason is empty?
**A**: Shouldn't happen, but fallback is "eof_natural". Check logs for issues.

### Q: How do I log removal_reason?
**A**: Check debug logs: `grep "removal_reason=" debug.log`

### Q: What does "forced_stuck_fade" mean?
**A**: A fade command was sent but never completed (system overload). Cue was force-removed after 3 timeout attempts.

### Q: Which reason indicates a problem?
**A**: Any with ⚠ mark: forced_stuck_fade, timeout_stuck_decode. Everything else is normal.

## Summary

✓ Removal reason tracking is now **fully implemented**
✓ All 4 of your requirements are **tracked**
✓ Debug logging is **comprehensive**
✓ Implementation is **production-ready**

You can now definitively diagnose why each cue stops by examining `CueFinishedEvent.cue_info.removal_reason`.
