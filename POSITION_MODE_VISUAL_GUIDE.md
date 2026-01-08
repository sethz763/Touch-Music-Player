# Position Mode Configuration - Visual Guide

## Simple Mode Comparison

### Mode 1: Trimmed Time (Default)
```
File:       |----0.5s----|====1.0s duration====|----|1.5s----|
Playback:                 ▶️═══════════════════▶️
Elapsed:                  0s        0.5s       1.0s
Remaining:                1.0s      0.5s       0.0s
User sees:                0:00      0:30       1:00
```

**Characteristics**:
- Elapsed starts at 0
- Looks natural to users
- Used in most applications

### Mode 2: Absolute File Position
```
File:       |----0.5s----|====1.0s duration====|----|1.5s----|
Playback:                 ▶️═══════════════════▶️
Elapsed:                  0.5s      1.0s       1.5s
Remaining:                1.0s      0.5s       0.0s
Developer sees:           0.5s      1.0s       1.5s
```

**Characteristics**:
- Elapsed shows file position
- More information for developers
- Used in editors/sync tools

---

## Data Flow Diagram

```
┌──────────────────────────────────────┐
│ AudioEngine (in separate process)    │
│ Calculates actual file position      │
│ elapsed_seconds = current_position   │
└──────────────┬───────────────────────┘
               │
               │ elapsed, remaining
               │ via event queue
               ▼
┌──────────────────────────────────────┐
│ EngineAdapter                        │
│                                      │
│ _position_relative_to_trim_markers   │
│        (True/False flag)             │
│                                      │
│ _calculate_trimmed_time()            │
│    ├─ If True:  trimmed time         │
│    └─ If False: absolute position    │
│                                      │
│ Return: (remaining, total)           │
└──────────────┬───────────────────────┘
               │
               │ cue_time signal
               │ (cue_id, elapsed,
               │  remaining, total)
               ▼
┌──────────────────────────────────────┐
│ GUI Widget (sound_file_button)       │
│ Displays: remaining_seconds          │
│ Shows: "0:30" or "0.5s" depending    │
│ on mode                              │
└──────────────────────────────────────┘
```

---

## Decision Tree

```
Is mode set to TRIMMED?
│
├─YES─────────────────────────────────────────┐
│                                             │
│ ┌──────────────────────────────────────┐   │
│ │ Use trimmed time formula:            │   │
│ │ remaining = (out - in) / sr - elapsed│   │
│ └──────────────────────────────────────┘   │
│                                             │
│ Result: elapsed starts at 0               │
│ Example: 0:00 → 1:00                      │
│                                             │
└─────────────────────────────────────────────┘

                    OR

├─NO──────────────────────────────────────────┐
│                                             │
│ ┌──────────────────────────────────────┐   │
│ │ Use absolute position formula:       │   │
│ │ remaining = (out / sr) - elapsed     │   │
│ │ (elapsed is file position, unchanged)│   │
│ └──────────────────────────────────────┘   │
│                                             │
│ Result: elapsed shows file position       │
│ Example: 0.5s → 1.5s                      │
│                                             │
└─────────────────────────────────────────────┘

              BOTH return
          (remaining, total)
              to GUI
```

---

## Timeline Visualization

### Scenario: Trimmed audio from 0.5s to 1.5s of a file

```
TRIMMED MODE:
──────────────────────────────────────
Time    |  Elapsed  | Remaining | Total
──────────────────────────────────────
0.0s    |   0:00    |   1:00    | 1:00
0.25s   |   0:15    |   0:45    | 1:00
0.5s    |   0:30    |   0:30    | 1:00
0.75s   |   0:45    |   0:15    | 1:00
1.0s    |   1:00    |   0:00    | 1:00
──────────────────────────────────────

ABSOLUTE MODE:
──────────────────────────────────────
Time    |  Elapsed  | Remaining | Total
──────────────────────────────────────
0.0s    |   0:30    |   1:00    | 1:00
0.25s   |   0:45    |   0:45    | 1:00
0.5s    |   1:00    |   0:30    | 1:00
0.75s   |   1:15    |   0:15    | 1:00
1.0s    |   1:30    |   0:00    | 1:00
──────────────────────────────────────

Note: Both modes have identical REMAINING
      values, just different ELAPSED!
```

---

## Configuration State Machine

```
    ┌─────────────────────┐
    │  EngineAdapter      │
    │  Created            │
    │  (default=TRIMMED)  │
    └──────────┬──────────┘
               │
               │ set_engine_position_relative_to_trim_markers(True)
               ▼
    ┌─────────────────────┐
    │  TRIMMED MODE       │ ◀─────────────┐
    │  (elapsed = 0 start)│              │
    └──────────┬──────────┘              │
               │                        │
               │ set_engine_position_   │
               │ relative_to_trim_      │
               │ markers(False)         │
               ▼                        │
    ┌─────────────────────┐             │
    │  ABSOLUTE MODE      │             │
    │  (elapsed = file    │─────────────┘
    │   position)         │
    └─────────────────────┘
```

---

## Use Case Flow

### Use Case 1: End User Playing Audio

```
User clicks play button
        │
        ▼
EngineAdapter (TRIMMED mode - default)
        │
        ▼ elapsed starts at 0
┌───────────────────┐
│ GUI shows:        │
│ 0:00 → 1:00      │ ← Natural progression
│ Remaining counts  │
│ down from 1:00    │
└───────────────────┘
        │
        ▼
User sees familiar time display
```

### Use Case 2: Developer Debugging

```
Time counter not resetting properly
        │
        ▼
Developer enables ABSOLUTE mode
adapter.set_engine_position_relative_to_trim_markers(False)
        │
        ▼
┌──────────────────────┐
│ Debug output shows: │
│ elapsed: 0.5 → 1.5  │ ← Shows file position
│ Remaining: 1.0 → 0  │
│                     │
│ Now developer can   │
│ verify engine is    │
│ reporting correct   │
│ file positions      │
└──────────────────────┘
```

---

## Implementation Architecture

```
┌─ gui/engine_adapter.py ──────────────────┐
│                                           │
│ class EngineAdapter:                      │
│   def __init__(...):                      │
│     self._position_relative_to_...  = True
│     ▲                                     │
│     │ Default: TRIMMED MODE              │
│     │                                     │
│   def set_engine_position_relative_      │
│       to_trim_markers(bool):              │
│     self._position_relative_to_...= bool  │
│     ▲                                     │
│     │ User can switch modes              │
│     │                                     │
│   def _calculate_trimmed_time(...):       │
│     if self._position_relative_to_...:    │
│       return trimmed_calculation()        │
│     else:                                 │
│       return absolute_calculation()       │
│     ▲                                     │
│     │ Different math per mode            │
│     │                                     │
│   Called by:                              │
│   - _emit_pending_time_events()           │
│   ▲                                       │
│   │ Results sent via cue_time signal     │
│   │                                       │
└─────────────────────────────────────────────┘
```

---

## Configuration File Example (Future)

```json
{
  "audio": {
    "position_mode": "trimmed",
    "comments": [
      "trimmed - elapsed starts at 0 (default)",
      "absolute - elapsed shows file position (debug only)"
    ]
  }
}
```

Usage:
```python
mode = config.get('audio', {}).get('position_mode', 'trimmed')
is_trimmed = (mode == 'trimmed')
adapter.set_engine_position_relative_to_trim_markers(is_trimmed)
```

---

## Mode Selection Guide

```
START HERE:
│
├─ Normal playback UI? → Use TRIMMED (default)
│                        ✓ Time looks natural
│                        ✓ Starts at 0:00
│                        ✓ No code needed
│
├─ Audio editor? → Use ABSOLUTE
│                  ✓ Shows file position
│                  ✓ Helps with sync
│                  ✓ call: set_...(False)
│
├─ Debugging time issues? → Try both
│                          ✓ Compare outputs
│                          ✓ Verify calculations
│                          ✓ Toggle to switch
│
└─ Not sure? → Use TRIMMED (default)
               ✓ Most intuitive
               ✓ Works for 99% of cases
```

---

## Test Scenario Matrix

```
┌─────────────────┬──────────────┬──────────────┐
│ Scenario        │ Trimmed Mode │ Absolute Mode│
├─────────────────┼──────────────┼──────────────┤
│ in=0.5s, out=1.5│              │              │
│ elapsed=0.5s    │ remaining=0.5│ remaining=1.0│
│                 │ total=1.0    │ total=1.0    │
├─────────────────┼──────────────┼──────────────┤
│ same, elapsed   │              │              │
│ =1.0s           │ remaining=0.0│ remaining=0.5│
│                 │ total=1.0    │ total=1.0    │
├─────────────────┼──────────────┼──────────────┤
│ no trim (full)  │              │              │
│ elapsed=1.0s    │ remaining=1.0│ remaining=1.0│
│                 │ total=2.0    │ total=2.0    │
├─────────────────┼──────────────┼──────────────┤
│ no trim, EOF    │              │              │
│ elapsed=2.0s    │ remaining=0.0│ remaining=0.0│
│                 │ total=2.0    │ total=2.0    │
└─────────────────┴──────────────┴──────────────┘
```

---

This visual guide supplements the other documentation files for understanding the position mode configuration at a glance.
