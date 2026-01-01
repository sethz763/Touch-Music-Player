# Cue Removal Reason Tracking - Implementation Summary

## Changes Made

### 1. **engine/cue.py** - Extended CueInfo dataclass
- **Added field**: `removal_reason: str = ""`
- **Purpose**: Track the specific reason why each cue was removed
- **Frozen**: Immutable field to preserve complete historical record

### 2. **engine/messages/events.py** - Updated CueFinishedEvent documentation
- **Updated docstring**: Added detailed list of all possible removal reasons
- **Reasons documented**:
  - `"eof_natural"`: Reached actual end of file
  - `"manual_stop"`: User clicked stop button
  - `"manual_fade"`: User initiated fade-out
  - `"auto_fade"`: Auto-faded when starting new track
  - `"forced_stuck_fade"`: Force-removed after failed fade attempt
  - `"decode_error: [message]"`: Decoder error occurred
  - `"timeout_stuck_decode"`: Output process timeout on stuck decoder
  - `"fade_complete"`: Fade envelope completed to silence

### 3. **engine/audio_engine.py** - Main tracking orchestration
- **Added instance variable**: `self._removal_reasons: Dict[str, str] = {}`
  - Tracks {cue_id: removal_reason_str} during playback
  - Cleared on engine shutdown
  
- **Updated stop_cue()**: 
  - Sets `_removal_reasons[cue_id] = "manual_stop"`
  - Logs removal_reason in metadata

- **Updated play_cue() auto-fade logic**:
  - Sets `_removal_reasons[cue_id] = "auto_fade"` for each faded cue
  - Logs removal_reason when fade is requested
  - Handles both staggered (high concurrency) and normal cases

- **Updated DecodeError handling**:
  - Sets `_removal_reasons[cue_id] = f"decode_error: {msg.error}"`
  - Captures specific error message for debugging

- **Updated pump() finished event processing**:
  - Retrieves removal_reason from output process (3rd element of tuple)
  - Falls back to _removal_reasons dict if set in engine
  - Creates final CueInfo with `removal_reason=reason`
  - Emits CueFinishedEvent with populated reason
  - Logs removal_reason in metadata

- **Updated forced cue removal**:
  - Sets `removal_reason = "forced_stuck_fade"`
  - Logs refade_attempts count for diagnostics

### 4. **engine/processes/output_process.py** - Output process tracking
- **Added tracking dict**: `removal_reasons: Dict[str, str] = {}`
  - Tracks removal reasons within output process scope
  
- **Updated DecodeError handling**:
  - Sets `removal_reasons[cue_id] = f"decode_error: {pcm.error}"`

- **Updated finished event emission**:
  - Retrieves reason from removal_reasons dict
  - Sends tuple: `("finished", cue_id, removal_reason)`
  - Default fallback: `"eof_natural"`
  - Logs removal reason with `[FINISHED]` tag

- **Added timeout tracking**:
  - Sets `removal_reasons[cue_id] = "timeout_stuck_decode"`
  - Tracks when cues are force-EOF'd due to decoder starvation

- **Added fade completion tracking**:
  - Sets `removal_reasons[cue_id] = "fade_complete"`
  - Tracks when fade envelopes complete naturally

### 5. **CUE_REMOVAL_TRACKING.md** - Comprehensive documentation
- Complete reference for all removal reasons
- Debug checklist for diagnosing premature EOF
- Log message examples for each removal path
- Troubleshooting guide with specific symptoms
- Future enhancement suggestions

## Data Flow

```
User Action / Condition
    ↓
Engine marks reason in _removal_reasons
    ↓
Engine sends command (OutputFadeTo, DecodeStop, etc.)
    ↓
Output process marks reason in removal_reasons
    ↓
Output process finishes ring, sends ("finished", cue_id, reason)
    ↓
Engine receives tuple, retrieves reason from both sources
    ↓
Engine creates final CueInfo with removal_reason
    ↓
CueFinishedEvent emitted with complete CueInfo
    ↓
GUI receives event with reason for analytics/logging
```

## Key Design Decisions

1. **Dual tracking (engine + output)**:
   - Engine tracks high-level decisions (manual commands, auto-fade logic)
   - Output tracks low-level conditions (EOF, timeout, fade completion)
   - Combining both provides complete picture

2. **Immutable CueInfo**:
   - Frozen dataclass preserves historical record
   - Each removal_reason is captured at removal time
   - No way to accidentally modify past removal reasons

3. **Backward compatibility**:
   - CueFinishedEvent.reason still populated (legacy field)
   - CueInfo.removal_reason is new, more detailed
   - Both contain same information for now

4. **Granular reasons**:
   - Not just "why did it stop" but "which path caused removal"
   - Allows diagnosing specific failure modes
   - Enables statistical analysis of removal patterns

## Diagnostics Enabled

With this system, you can now:

1. **Find cues stopping early**:
   ```bash
   grep "removal_reason=eof_natural" logs/ | grep -v "10\\.0[0-9]s"  # Find premature EOF
   ```

2. **Count removal types**:
   ```bash
   grep "removal_reason=" logs/ | cut -d= -f2 | sort | uniq -c
   ```

3. **Find stuck cues**:
   ```bash
   grep "removal_reason=forced_stuck_fade\|timeout_stuck_decode" logs/
   ```

4. **Debug specific cue**:
   ```bash
   grep "cue=ABC123" logs/ | grep -E "removal_reason|FINAL|out_frame"
   ```

## Testing Recommendations

1. **Manual stop**: Start a cue, click stop → should see `removal_reason=manual_stop`

2. **Auto-fade**: Start cue1, then start cue2 with auto_fade_on_new=True → cue1 should have `removal_reason=auto_fade`

3. **Natural EOF**: Long file, let it play to end → should see `removal_reason=eof_natural`

4. **Decode error**: Try to play corrupted/missing file → should see `removal_reason=decode_error: [error]`

5. **Stuck fade**: (Difficult to reproduce intentionally) If fade doesn't complete, should eventually see `removal_reason=forced_stuck_fade`

## Notes for Debugging Premature EOF

The implementation provides the answer to your original problem. If cues are stopping before the end:

1. **Check removal_reason**: Is it actually `eof_natural` or something else?
   - If `auto_fade`: A new cue was started
   - If `forced_stuck_fade`: Output process hung
   - If `timeout_stuck_decode`: Decoder starved

2. **Check out_frame**: Look for log `FINAL: out_frame=X`
   - If X is too low → file probe failed
   - If X is correct but cue stops early → check ring.eof setting

3. **Check removal_reason with stop event timing**: 
   - See CUE_REMOVAL_TRACKING.md for detailed log patterns

## Future Enhancements

- Add removal_reason to CueTimeEvent for real-time tracking
- Add GUI dashboard showing removal reason statistics
- Add automatic alerts for unusual patterns (e.g., too many timeouts)
- Export removal_reason to analytics/telemetry system
