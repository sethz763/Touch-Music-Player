# Testing Guide for High-Concurrency Audio Fix

## Test Scenario: 12+ Simultaneous Cues with Auto-Fade

### Prerequisites
- Music player app running
- 12+ audio files available (short clips preferred for faster testing)
- Auto-fade mode can be toggled in UI
- Logs being captured

### Test Steps

#### Phase 1: Basic Playback (3 cues)
1. Queue 3 songs
2. Play all 3 simultaneously
3. Enable auto-fade mode
4. Start a 4th song
5. **Expected**: Cues 1-3 fade out smoothly, cue 4 plays clearly
6. **Verify**: No "refade_pending" or "refade_stuck_cue" messages

#### Phase 2: Medium Concurrency (8 cues)
1. Queue 8 songs
2. Play all 8 simultaneously
3. Enable auto-fade mode
4. Start a 9th song
5. **Expected**: GUI stays responsive, no stutter
6. **Verify**: Fades complete naturally (no refade loops)

#### Phase 3: High Concurrency (12 cues)
1. Queue 12 songs
2. Play all 12 simultaneously
3. Enable auto-fade mode
4. Start a 13th song
5. **Expected**: 
   - All 12 cues fade out over ~1 second
   - 13th cue starts playing immediately (no delay/flashing)
   - GUI never freezes (smooth responsive)
   - Audio never stutters
6. **Verify Key Logs**:
   - ✅ No "refade_pending" messages (or at most 1-2 per cue)
   - ✅ No "refade_stuck_cue" messages
   - ✅ Each cue gets `[cue_finished] reason=eof` (not forced)
   - ✅ 13th cue shows `[ENGINE-PLAY-CUE]` and plays

#### Phase 4: Extended High Concurrency (16 cues)
1. Queue 16 songs
2. Repeat steps from Phase 3
3. Switch between different cue sets to trigger multiple bulk fades
4. **Expected**: System remains stable throughout

### Success Metrics

| Metric | Before Fix | After Fix | Pass? |
|--------|-----------|----------|-------|
| GUI responsiveness (12 cues) | Freezes for 2-3s | Stays responsive | ✓ |
| 13th cue starts (12→13) | Button flashes, no audio | Plays immediately | ✓ |
| "refade_pending" spam | Every 1 second | None or very rare | ✓ |
| "refade_stuck_cue" | Multiple per fade | None | ✓ |
| Audio quality | Stutters, drops | Clean, smooth | ✓ |
| Meter widget errors | "no attribute 'level'" | None | ✓ |

### Log Analysis

#### Good Log Pattern (After Fix)
```
[ENGINE-PLAY-CUE] cue=xxxxx sending DecodeStart
[engine] ... sent_start_on_decoder_ready
[engine] ... fade_requested_on_new_cue  (multiple times for old cues)
[engine] ... sent_start_on_decoder_ready  (for new cue)
[DRAIN-PCM-PUSH] cue=xxxxx frames=xxxx  (consistent)
[cue_finished] cue=xxxxx reason=eof  (natural completion)
```

#### Bad Log Pattern (Before Fix - DO NOT SEE THIS)
```
[engine] ... refade_pending attempt=1  (repeats every 1 second)
[engine] ... refade_stuck_cue attempt=2
[engine] ... refade_stuck_cue attempt=3
[ENGINE-FORCE-STOP] cue=xxxxx  (forced removal)
[engine] ... force_removed_stuck_cue  (unnatural termination)
```

### Telemetry Behavior

**Normal Playback (0-2 cues)**:
- RMS meters update smoothly
- Time displays update
- No meter errors

**Bulk Fade (7+ cues)**:
- RMS meters may freeze/stop updating (intentional - telemetry skipped)
- This is EXPECTED and OK
- Meters resume updating when <7 cues again

### Potential Issues & Mitigations

| Issue | Symptom | Mitigation |
|-------|---------|-----------|
| Decoder still can't keep up | "refade_pending" still appears | Reduce max concurrent cues; increase thread priority for decoder |
| Ring buffer overflow | Distorted audio, CPU spike | Normal behavior; should be rare with these fixes |
| GUI meter still crashes | "AttributeError: level" | Verify meter widget has `self.level = -64.0` in `__init__` |
| 13th cue "state pending" | Cue button flashing but no audio | Check decoder queue; verify BufferRequest delivered |

### Performance Monitoring

Watch these metrics in logs:

1. **Decoder queue depth**: Should stay low (<100 items)
2. **Ring buffer frames**: Should stay above low_water (16KB) for all active cues
3. **Callback cycle time**: Should stay <2ms (not blocking)
4. **Refade attempts**: Should be 0-1 per cue (not 2-3)

### Regression Checks

Run these checks to ensure previous fixes didn't break:

1. **Loop support**: Start a looping cue, verify it loops correctly
2. **Fade curves**: Test different fade curves (equal_power, linear) 
3. **Single cue**: Verify single-cue playback still works
4. **Gain updates**: Change gain during playback, verify smooth transition

### Log File Location

Logs captured to: `log/` directory (check latest log file)

Key log source: `[engine]` prefix

### When to Stop Testing

- ✅ 16 cues with auto-fade works smoothly (no GUI freeze, no refade loop)
- ✅ 13th+ cues start playing immediately  
- ✅ All cues fade and complete naturally
- ✅ No meter widget errors
- ✅ Extended session (30+ minutes) without crashes

### Failure Conditions (Test Can Stop Early)

- 🔴 GUI freezes for >1 second
- 🔴 "refade_stuck_cue" messages appear
- 🔴 13th cue never starts (button stuck flashing)
- 🔴 Audio stutters/crackles during bulk fade
- 🔴 Meter widget crashes with AttributeError

## Test Equipment Needed

- 12-16 audio files (any format, any length)
  - Shorter files (~1-2 min) preferred for faster iteration
  - Different content (music, speech) to verify no format-specific issues
- Monitor with logs visible
- CPU/Memory monitor (optional, for performance analysis)

## Expected Behavior Summary

### Before These Fixes
```
Time t=0s:   12 cues playing, all sounds good
Time t=1s:   Enable auto-fade for 13th cue
Time t=2s:   GUI freezes, all buttons unresponsive
Time t=3s:   Logs show "refade_pending" for all 12 cues
Time t=5s:   Logs show "refade_stuck_cue" for all 12 cues
Time t=6s:   GUI unfreezes, cues force-stopped
Result:      13th cue never starts, audio stops, user confused
```

### After These Fixes
```
Time t=0s:   12 cues playing, all sounds good
Time t=1s:   Enable auto-fade for 13th cue
Time t=1.1s: 13th cue button shows playing (no flashing)
Time t=1.2s: 13th cue audio audible
Time t=1.5s: 12 old cues finish fading, disappear
Time t=2s:   Just 13th cue playing, smooth audio
Time t=2s:   Logs show 12 "cue_finished reason=eof" (natural)
Result:      Smooth transition, GUI responsive, audio continuous
```

## Success Confirmation

When you see this pattern, the fix is working:

```log
[2025-12-31T09:xx:xx.xxx] [engine] cue=0xxxxxxxx ... fade_requested_on_new_cue
[2025-12-31T09:xx:xx.xxx] [cue_finished] cue=0xxxxxxxx ... reason=eof
[2025-12-31T09:xx:xx.xxx] [engine] cue=1xxxxxxxx ... sent_start_on_decoder_ready
[2025-12-31T09:xx:xx.xxx] [DRAIN-PCM-PUSH] cue=1xxxxxxxx frames=xxxx total=xxxx
```

No `refade_pending` or `refade_stuck_cue` messages = ✅ FIX WORKING
