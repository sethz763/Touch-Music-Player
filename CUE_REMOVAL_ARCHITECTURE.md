# Cue Removal Tracking Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                          GUI (Main Thread)                       │
│                                                                   │
│  PlayCueCommand → play_cue()                                    │
│  StopCueCommand → stop_cue()                                    │
│  FadeCueCommand → handle_command()                              │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│                    AudioEngine (Main Thread)                     │
│                                                                   │
│  Tracking Dict: _removal_reasons = {cue_id: reason}            │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ play_cue()                                              │   │
│  │  - auto_fade existing cues                             │   │
│  │  - _removal_reasons[cue] = "auto_fade"  ← TRACKS      │   │
│  │  - sends DecodeStart + OutputStartCue                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ stop_cue()                                              │   │
│  │  - _removal_reasons[cue] = "manual_stop"  ← TRACKS    │   │
│  │  - sends DecodeStop + OutputStopCue                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ handle_command(DecodeError)                             │   │
│  │  - _removal_reasons[cue] = f"decode_error: {msg}"     │   │
│  │  - sends OutputStopCue                                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ pump() - Main event processing loop                     │   │
│  │  - Receives ("finished", cue_id, output_reason)       │   │
│  │  - Gets engine reason from _removal_reasons            │   │
│  │  - Chooses: engine_reason OR output_reason            │   │
│  │  - Creates final CueInfo with removal_reason          │   │
│  │  - Emits CueFinishedEvent                             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
          ↓ commands (DecodeStart, OutputStartCue, etc.)
          ↓ ← events (("finished", cue_id, reason))
┌─────────────────────────────────────────────────────────────────┐
│              Output Process (Subprocess)                         │
│                                                                   │
│  Tracking Dict: removal_reasons = {cue_id: reason}             │
│                                                                   │
│  Audio Callback:                                               │
│    ring.eof = True                                            │
│    ring.finished_pending = True                              │
│                                                                   │
│  Main Loop:                                                    │
│    ┌───────────────────────────────────────────────────────┐  │
│    │ Process DecodeError:                                  │  │
│    │  - removal_reasons[cue] = f"decode_error: {msg}"    │  │
│    │  - ring.eof = True                                   │  │
│    └───────────────────────────────────────────────────────┘  │
│                                                                   │
│    ┌───────────────────────────────────────────────────────┐  │
│    │ Fade envelope completed:                              │  │
│    │  - removal_reasons[cue] = "fade_complete"           │  │
│    │  - Send DecodeStop                                   │  │
│    └───────────────────────────────────────────────────────┘  │
│                                                                   │
│    ┌───────────────────────────────────────────────────────┐  │
│    │ Timeout cleanup (stuck cue):                          │  │
│    │  - removal_reasons[cue] = "timeout_stuck_decode"    │  │
│    │  - ring.eof = True                                   │  │
│    └───────────────────────────────────────────────────────┘  │
│                                                                   │
│    ┌───────────────────────────────────────────────────────┐  │
│    │ Emit finished events:                                 │  │
│    │  - Get reason from removal_reasons (or default)      │  │
│    │  - Send: ("finished", cue_id, removal_reason)  ← YES │  │
│    │  - Cleanup: pop from removal_reasons                 │  │
│    └───────────────────────────────────────────────────────┘  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
          ↓ ("finished", cue_id, reason_from_output)
          ↓
┌─────────────────────────────────────────────────────────────────┐
│              Decoder Process (Subprocess)                        │
│                                                                   │
│  Sends: DecodeError(cue_id, error_message)                     │
│  Sends: DecodedChunk(cue_id, pcm_data, eof=True)              │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
          ↓
          ↓ DecodeError, DecodedChunk
          ↓
┌─────────────────────────────────────────────────────────────────┐
│                    CueInfo (Frozen Snapshot)                    │
│                                                                   │
│  @dataclass(frozen=True, slots=True)                           │
│  class CueInfo:                                                 │
│      cue_id: str                                               │
│      track_id: str                                             │
│      file_path: str                                            │
│      duration_seconds: Optional[float]                         │
│      in_frame: int                                             │
│      out_frame: Optional[int]                                  │
│      gain_db: float                                            │
│      fade_in_ms: int                                           │
│      fade_out_ms: int                                          │
│      metadata: dict | None                                     │
│      started_at: Optional[datetime]                            │
│      stopped_at: Optional[datetime]                            │
│      loop_enabled: bool                                        │
│      removal_reason: str = ""  ← TRACKS WHY REMOVED           │
│                                                                   │
│  Removal Reason Values:                                         │
│  ├─ "eof_natural": Reached end of file (no loop)              │
│  ├─ "manual_stop": User clicked stop                          │
│  ├─ "manual_fade": User initiated fade (inferred)             │
│  ├─ "auto_fade": Auto-faded for new track                     │
│  ├─ "forced_stuck_fade": Force-removed after timeout          │
│  ├─ "decode_error: [msg]": Decoder error with message        │
│  ├─ "timeout_stuck_decode": Output timeout                    │
│  └─ "fade_complete": Fade envelope completed                  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
          ↓
┌─────────────────────────────────────────────────────────────────┐
│                    CueFinishedEvent                             │
│                                                                   │
│  @dataclass(frozen=True, slots=True)                           │
│  class CueFinishedEvent:                                        │
│      cue_info: CueInfo  ← Contains removal_reason              │
│      reason: str  ← Legacy field (also contains reason)        │
│                                                                   │
│  GUI can access removal_reason via:                            │
│      event.cue_info.removal_reason                             │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
          ↓
┌─────────────────────────────────────────────────────────────────┐
│                    GUI (Main Thread)                             │
│                                                                   │
│  on_cue_finished(event: CueFinishedEvent):                     │
│      reason = event.cue_info.removal_reason                   │
│      match reason:                                             │
│          case "eof_natural":                                   │
│              print("Played to end")                            │
│          case "auto_fade":                                     │
│              print("Faded out for new track")                 │
│          case "decode_error":                                  │
│              print("Error:", reason)                           │
│          ...                                                    │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

## Removal Reason Decision Tree

```
Cue Started
    ↓
    ├─ User clicks STOP?
    │  └─→ removal_reason = "manual_stop"
    │
    ├─ New cue started (auto_fade_on_new=True)?
    │  └─→ removal_reason = "auto_fade" (for existing cues)
    │
    ├─ Decoder encounters ERROR?
    │  └─→ removal_reason = "decode_error: [msg]"
    │
    ├─ Fade envelope completes to silence?
    │  └─→ removal_reason = "fade_complete"
    │
    ├─ Output timeout (no PCM for 30s)?
    │  └─→ removal_reason = "timeout_stuck_decode"
    │      └─→ (if system forcibly removes after 3 retries)
    │          removal_reason = "forced_stuck_fade"
    │
    └─ Reaches out_frame with loop_disabled?
       └─→ removal_reason = "eof_natural"
```

## Log Flow Example: Natural EOF

```
Engine Thread:
  [AUDIO-ENGINE] cue=ABC123 play_cue requested
  [AUDIO-ENGINE] cue=ABC123 FINAL: out_frame=480000

Output Thread:
  [START-CUE] cue=ABC123 is_new=True fade_in=100
  [DRAIN-FADE-IN] cue=ABC123 fade_in=4800fr
  [DRAIN-PCM-PUSH] cue=ABC123 frames=960 ring.eof=False
  [DRAIN-PCM-PUSH] cue=ABC123 frames=960 ring.eof=False
  ... (many PCM chunks) ...
  [DRAIN-PCM-PUSH] cue=ABC123 frames=480 ring.eof=True ← Last chunk

Engine Thread (callback in audio.c):
  ring.finished_pending = True ← Mark as done

Output Thread (main loop):
  [FINISHED] cue=ABC123 removal_reason=eof_natural ← TRACKED
  event_q.put(("finished", "ABC123", "eof_natural"))

Engine Thread (pump):
  Receives ("finished", "ABC123", "eof_natural")
  _removal_reasons.pop("ABC123") not set → use "eof_natural"
  final_cue_info = CueInfo(..., removal_reason="eof_natural")
  evts.append(CueFinishedEvent(cue_info=final_cue_info, reason="eof_natural"))
  log: cue_finished with removal_reason=eof_natural

GUI Thread:
  on_cue_finished(event)
  reason = event.cue_info.removal_reason  # "eof_natural"
  → Display "Song finished" or analytics record
```

## Log Flow Example: Auto-Fade

```
Engine Thread:
  [AUDIO-ENGINE] cue=OLD play_cue done
  ... later ...
  [AUDIO-ENGINE] cue=NEW play_cue requested
  _removal_reasons[OLD] = "auto_fade" ← TRACKED
  fade_requested_on_new_cue removal_reason=auto_fade

Output Thread:
  OutputFadeTo(cue_id=OLD, target_db=-120.0, duration_ms=1000)
  cur = 1.0 (linear gain)
  envelopes[OLD] = _FadeEnv(1.0, 0.0, 48000, "equal_power")
  
  [CALLBACK-FADE] cue=OLD gain=0.95 → 0.85 → ... → 0.00
  ring.finished_pending = True ← Fade completed

Engine Thread (pump):
  Receives ("finished", "OLD", "eof_natural")  ← No reason set in output
  removal_reason = _removal_reasons.pop("OLD")  # "auto_fade" ← USE ENGINE REASON
  final_cue_info = CueInfo(..., removal_reason="auto_fade")
  evts.append(CueFinishedEvent(cue_info=final_cue_info, reason="auto_fade"))
  log: cue_finished with removal_reason=auto_fade

GUI Thread:
  on_cue_finished(event)
  reason = event.cue_info.removal_reason  # "auto_fade"
  → Record analytics: "transitioned to new track"
```

## Reason Priority (Engine vs Output)

When both engine and output set a reason, **engine reason takes priority**:

```python
# In audio_engine.pump():
removal_reason = self._removal_reasons.pop(cue_id, output_removal_reason)
#                ↑ engine reason preferred
#                                             ↑ fallback to output
```

**Rationale**: Engine makes high-level decisions (commands, auto-fade logic), output reports low-level conditions (EOF, timeout). Engine decision is more semantically meaningful.

## Data Flow Diagram

```
User Action                 System Reaction              Reason Tracked
─────────────              ─────────────────             ──────────────

play_cue(Cue2)  ─────→     auto-fade(Cue1)  ─────→     "auto_fade"
                           requests fade
                           
stop_button     ─────→     stop_cue()       ─────→     "manual_stop"
                           sends DecodeStop
                           
decode error    ─────→     DecodeError      ─────→     "decode_error: [msg]"
in decoder               sent to engine
                           
fade envelope   ─────→     Silence reached  ─────→     "fade_complete"
completes                  DecodeStop sent
                           
no PCM for 30s  ─────→     Timeout cleanup  ─────→     "timeout_stuck_decode"
                           mark ring.eof
                           
reach out_frame ─────→     Ring finished    ─────→     "eof_natural"
& !loop              	   (naturally)
```

## Implementation Checklist

- [x] CueInfo.removal_reason field added
- [x] AudioEngine._removal_reasons dict tracking
- [x] OutputProcess.removal_reasons dict tracking
- [x] All 4 required conditions tracked
- [x] Additional edge cases tracked
- [x] Final CueInfo created with removal_reason
- [x] CueFinishedEvent includes removal_reason
- [x] Debug logs with removal_reason metadata
- [x] Documentation complete
- [x] Backward compatible
