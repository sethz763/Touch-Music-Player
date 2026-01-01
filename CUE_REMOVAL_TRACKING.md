# Cue Removal Tracking System

## Overview

This document describes the granular cue removal tracking system implemented to diagnose why cues are being prematurely marked as EOF and dropped.

## Valid Removal Reasons

Cues can only be removed from the audio_engine processes for these documented reasons:

### 1. **eof_natural** (Natural end of file)
- **Condition**: Cue reaches actual end of file AND loop is disabled
- **Location**: output_process.py - when `ring.eof=True` and `ring.frames==0` and cue not in `looping_cues`
- **Log Message**: `[FINISHED] cue=... removal_reason=eof_natural`
- **Debug Info**: Check if `out_frame` is correctly set in decoder. If `out_frame` is too low, premature EOF will occur.

### 2. **manual_stop** (Manual stop from GUI)
- **Condition**: User clicks stop button, `StopCueCommand` received
- **Location**: audio_engine.py - `stop_cue()` method
- **Log Message**: `cue_stop_requested with removal_reason=manual_stop`
- **Expected**: Immediate cleanup, no fade-out unless explicitly requested

### 3. **manual_fade** (Manual fade from GUI)
- **Condition**: User initiates fade-out via FadeCueCommand
- **Location**: audio_engine.py - `handle_command()` processes FadeCueCommand
- **Log Message**: `fade_requested with target_db=-120.0`
- **Expected**: Cue fades to silence then removed

### 4. **auto_fade** (Auto-fade when starting new track)
- **Condition**: New cue started with `auto_fade_on_new=True` (default)
- **Location**: audio_engine.py - `play_cue()` method auto-fades existing cues
- **Log Message**: `fade_requested_on_new_cue with removal_reason=auto_fade`
- **Expected**: Existing cues fade out as new track begins

### 5. **forced_stuck_fade** (Force-removal after failed fade)
- **Condition**: Cue fade-out command sent, but cue doesn't finish within timeout (3+ attempts)
- **Location**: audio_engine.py - `pump()` method refade timeout logic
- **Log Message**: `force_removed_stuck_cue with refade_attempts=X removal_reason=forced_stuck_fade`
- **Debug Info**: Indicates audio callback is stalled or output process has deadlocked

### 6. **decode_error: [error message]** (Decoder error)
- **Condition**: Audio decode fails (file corruption, unsupported format, file read error)
- **Location**: audio_engine.py - handles `DecodeError` from decoder process
- **Log Message**: `[ENGINE-DECODE-ERROR] cue=... DecodeError: [specific error]`
- **Expected**: `DecodeErrorEvent` emitted to GUI

## Tracking Mechanism

### In CueInfo dataclass:
```python
@dataclass(frozen=True, slots=True)
class CueInfo:
    # ... existing fields ...
    removal_reason: str = ""  # "eof_natural", "manual_stop", "auto_fade", etc.
```

### In AudioEngine:
```python
self._removal_reasons: Dict[str, str] = {}  # Track {cue_id: reason_str}
```

### In OutputProcess:
```python
removal_reasons: Dict[str, str] = {}  # Track {cue_id: reason_str}
```

### Event Flow:
1. **Engine marks reason**: When fade/stop/error occurs in audio_engine, store reason in `_removal_reasons[cue_id]`
2. **Output reports reason**: When ring finishes, output_process sends: `("finished", cue_id, removal_reason)`
3. **Engine finalizes**: `pump()` creates final CueInfo with removal_reason and emits CueFinishedEvent
4. **GUI receives**: CueFinishedEvent.cue_info.removal_reason contains the reason

## Debug Log Checklist

To diagnose premature EOF issues, look for these log messages in order:

### Case 1: Cue finishing too early (not reaching out_frame)
```
[AUDIO-ENGINE] cue=ABC123 FINAL: out_frame=480000 (from total_seconds=10.0 sample_rate=48000)
// ... cue plays ...
[FINISHED] cue=ABC123 removal_reason=eof_natural  // Should be after ~10 seconds
// If appears much sooner: out_frame is wrong or decoder is cutting early
```

**Check:**
- `out_frame` value is reasonable for the file
- `total_seconds` from file probe matches actual duration
- Decoder is not hitting a boundary condition early

### Case 2: Auto-fade removing active cues
```
cue_start_requested for cue_id=NEW_CUE
fade_requested_on_new_cue removal_reason=auto_fade  // For each existing cue
[FINISHED] cue=OLD_CUE removal_reason=auto_fade
```

**Expected:** Existing cues fade out gracefully

### Case 3: Stuck fade timeout
```
fade_requested_on_new_cue removal_reason=auto_fade
refade_pending with attempt=1
refade_stuck_cue with attempt=2
force_removed_stuck_cue with refade_attempts=3 removal_reason=forced_stuck_fade
[FINISHED] cue=STUCK_CUE removal_reason=forced_stuck_fade
```

**Indicates:** Audio callback or output process is overloaded

### Case 4: Decode error
```
[ENGINE-DECODE-ERROR] cue=ABC123 DecodeError: [error details]
// DecodeErrorEvent sent to GUI
// Cue removed with removal_reason=decode_error: [error]
```

## Command-line Testing

To view removal reason debug logs:
```bash
grep "removal_reason" debug_logs/*.log
```

Filter by specific reasons:
```bash
grep "removal_reason=eof_natural" debug_logs/*.log  # Natural endings only
grep "removal_reason=forced_stuck_fade" debug_logs/*.log  # Stuck cues
grep "decode_error:" debug_logs/*.log  # All decode errors
```

## Implementation Details

### Why CueInfo.removal_reason?
The frozen CueInfo dataclass allows the GUI to track why each cue was removed. This provides:
- Historical record of all playback completions
- Audit trail for debugging
- Data for analytics (how many cues forced-removed? how many timeout?)

### Why track in both engine and output process?
- **Engine** knows about manual commands, auto-fade decisions, errors
- **Output** knows about actual ring finish conditions, EOF conditions
- **Combining both** gives complete picture of cue lifecycle

### Backward Compatibility
The `CueFinishedEvent.reason` field (legacy) is still populated, but the more detailed `CueInfo.removal_reason` is preferred for new code.

## Troubleshooting Premature EOF

### Symptom: Cues stop before reaching end of file

1. **Check out_frame calculation**:
   - Look for log: `FINAL: out_frame=X`
   - Verify X matches actual file duration in frames
   - If out_frame is too low → file probe failed or total_seconds wrong

2. **Check decoder isn't hitting hard limit**:
   - Search for decode errors
   - Check file integrity with external tool (ffprobe, mediainfo)

3. **Check removal_reason**:
   - If `removal_reason=eof_natural` but cue stopped early → out_frame problem
   - If `removal_reason=forced_stuck_fade` → audio callback overloaded
   - If `removal_reason=auto_fade` → new cue started (expected)

4. **Check concurrent load**:
   - If many cues forced-removed → system overloaded
   - Consider reducing concurrent cue count or increasing buffer sizes

## Future Enhancements

- Add removal_reason to CueTimeEvent for real-time tracking during playback
- Add removal_reason statistics dashboard to GUI
- Add auto-alert for unusual removal reasons (too many forced_stuck_fade events)
