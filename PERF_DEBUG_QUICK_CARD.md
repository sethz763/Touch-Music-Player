# Performance Debug Quick Card

## What's Instrumented

| Layer | File | Methods | Status |
|-------|------|---------|--------|
| **Engine Adapter** | `gui/engine_adapter.py` | play_cue, stop_cue, fade_cue, _poll_events, _dispatch_event | ✅ Fast (< 1ms) |
| **Button Bank** | `ui/widgets/button_bank_widget.py` | _on_adapter_cue_started/finished/time/levels | 🔍 Check output |
| **Button Widget** | `ui/widgets/sound_file_button.py` | _on_cue_started/finished/time/levels | 🔍 Check output |

---

## Console Output Format

```
[PERF] ClassName.method_name: X.XXms cue_id=... [extra info]
```

**Normal (< 2ms):**
```
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.34ms cue_id=abc reason=eof
[PERF] SoundFileButton._on_cue_finished: 1.15ms cue_id=abc reason=eof
```

**Slow (> 2ms):**
```
[PERF] SoundFileButton._on_cue_finished: 15.67ms cue_id=abc reason=eof  ← Problem found!
```

---

## How to Reproduce Freeze

```
1. Start GUI
2. Load 5+ audio files
3. Play them all (buttons 1-5)
4. While playing, fade them all out
5. IMMEDIATELY play new cue on empty button
6. FREEZE HAPPENS HERE
7. Check console for [PERF] messages
```

---

## Expected Results

### Good (No Freeze)
```
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.40ms cue_id=1 reason=eof
[PERF] SoundFileButton._on_cue_finished: 0.80ms cue_id=1 reason=eof
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.38ms cue_id=2 reason=eof
[PERF] SoundFileButton._on_cue_finished: 0.75ms cue_id=2 reason=eof
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.42ms cue_id=3 reason=eof
[PERF] SoundFileButton._on_cue_finished: 0.82ms cue_id=3 reason=eof
Total: 3 x 2ms = 6ms (single frame, no freeze)
```

### Bad (Freeze)
```
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.40ms cue_id=1 reason=eof
[PERF] SoundFileButton._on_cue_finished: 14.23ms cue_id=1 reason=eof ← SLOW!
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.38ms cue_id=2 reason=eof
[PERF] SoundFileButton._on_cue_finished: 15.67ms cue_id=2 reason=eof ← SLOW!
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.42ms cue_id=3 reason=eof
[PERF] SoundFileButton._on_cue_finished: 13.89ms cue_id=3 reason=eof ← SLOW!
Total: 3 x 15ms = 45ms (3 frames frozen!)
```

---

## What to Look For

### Key Metrics
- **Each cue finish:** Should be < 2ms total
- **Multiple cues:** Total time should be < 16ms (60fps frame budget)
- **Level updates:** Should be < 2ms each

### Red Flags
- Any single operation > 5ms
- Multiple operations > 10ms total
- Consistent slowness (not random spikes)

---

## Instrumentation Locations

**ButtonBankWidget** (lines ~195-220):
```python
def _on_adapter_cue_finished(self, cue_id: str, cue_info: object, reason: str) -> None:
    start = time.perf_counter()
    # ... routing logic ...
    elapsed = (time.perf_counter() - start) * 1000
    if elapsed > self._slow_threshold_ms:
        print(f"[PERF] ButtonBankWidget._on_adapter_cue_finished: {elapsed:.2f}ms cue_id={cue_id} reason={reason}")
```

**SoundFileButton** (lines ~1060-1085):
```python
def _on_cue_finished(self, cue_id: str, cue_info: object, reason: str) -> None:
    start = time.perf_counter()
    # ... UI update logic ...
    elapsed = (time.perf_counter() - start) * 1000
    if elapsed > 2.0:
        print(f"[PERF] SoundFileButton._on_cue_finished: {elapsed:.2f}ms cue_id={cue_id} reason={reason}")
```

---

## Threshold

Current: **2.0ms** for all widget handlers

To see more detail (noisier):
```python
# In ButtonBankWidget.__init__:
self._slow_threshold_ms = 0.5  # Show everything > 0.5ms

# In SoundFileButton methods, change:
if elapsed > 0.5:  # Instead of > 2.0
```

---

## Once You Have Data

1. **Copy all [PERF] messages from console**
2. **Note the slowest operation**
3. **Note how many times it appears**
4. **Share output**

Example format to share:
```
Reproducing freeze scenario with 5 cues finishing simultaneously:

[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.41ms cue_id=1 reason=eof
[PERF] SoundFileButton._on_cue_finished: 16.23ms cue_id=1 reason=eof
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.38ms cue_id=2 reason=eof
[PERF] SoundFileButton._on_cue_finished: 15.67ms cue_id=2 reason=eof
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.40ms cue_id=3 reason=eof
[PERF] SoundFileButton._on_cue_finished: 17.34ms cue_id=3 reason=eof
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.39ms cue_id=4 reason=eof
[PERF] SoundFileButton._on_cue_finished: 16.89ms cue_id=4 reason=eof
[PERF] ButtonBankWidget._on_adapter_cue_finished: 0.42ms cue_id=5 reason=eof
[PERF] SoundFileButton._on_cue_finished: 15.45ms cue_id=5 reason=eof

Total: ~85ms GUI freeze (5 frames skipped at 60fps)
Slowest: SoundFileButton._on_cue_finished at 17.34ms
```

---

## Files to Monitor

1. **gui/engine_adapter.py** - Baseline (should be < 1ms)
2. **ui/widgets/button_bank_widget.py** - Routing layer (should be < 1ms)
3. **ui/widgets/sound_file_button.py** - Button updates (likely culprit, watch for > 2ms)

---

## That's It!

Run your test scenario, capture the output, and we'll identify and fix the exact bottleneck.

