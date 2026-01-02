# GUI Performance Debugging Guide

## Quick Start

The audio engine adapter now has comprehensive timing instrumentation. When you run the GUI and trigger the problematic scenario (multiple cues fading out + new cue starting), you'll see `[PERF]` messages in the console output.

### Running the Test

```bash
python test_fade_performance.py
```

Or use your normal GUI and look for `[PERF]` messages when you reproduce the lock-up.

---

## What to Look For

### 1. **Command Queue Operations** (Should be < 1ms each)

```
[PERF] play_cue took 0.15ms (queue.put: 0.05ms) cue_id=abc123
[PERF] fade_cue took 0.18ms (queue.put: 0.07ms) cue_id=abc123 target=-60dB
[PERF] stop_cue took 0.12ms (queue.put: 0.06ms) cue_id=abc123
```

**Good:** All under 1ms
**Bad:** Any over 5ms → queue.put() is blocking (OS issue or queue contention)

---

### 2. **Event Polling** (Should be < 5ms per poll cycle)

```
[PERF] _poll_events: 2.34ms (8 events, max event 0.45ms, telemetry 0.12ms) avg10=1.89ms
```

Breaking this down:
- `2.34ms` = Total time to poll and dispatch all events
- `8 events` = Number of events received in this poll cycle
- `max event 0.45ms` = Slowest individual event dispatch
- `telemetry 0.12ms` = Time to emit throttled telemetry
- `avg10=1.89ms` = Average over last 10 poll cycles

**Good:** < 5ms (single event) or < 10ms total for multiple events
**Bad:** > 16ms (would skip a frame at 60fps)

---

### 3. **Individual Event Dispatch** (Should be < 1ms each)

```
[PERF] _dispatch_event CueFinishedEvent: 0.32ms
[PERF] _dispatch_event BatchCueLevelsEvent: 0.18ms
[PERF] _dispatch_event CueTimeEvent: 0.14ms
```

**Good:** All under 1ms
**Bad:** Any over 5ms → Signal emission is slow (too many connected slots)

---

## How to Reproduce the Lock-Up

1. **Start multiple cues:** Click buttons to play 5+ cues
2. **Stop/fade them:** Select multiple and use stop or fade buttons
3. **While fading, start new cue:** This is when the lock-up happens
4. **Watch console:** Look for `[PERF]` messages and their timing

---

## Analyzing the Output

### Scenario A: Slow Queue Operations
```
[PERF] fade_cue took 15.34ms (queue.put: 14.92ms) cue_id=abc123
[PERF] fade_cue took 16.12ms (queue.put: 15.87ms) cue_id=def456
[PERF] fade_cue took 14.78ms (queue.put: 14.45ms) cue_id=ghi789
```

**Diagnosis:** `queue.put()` is taking 15ms!
**Cause:** OS multiprocessing queue is slow (usually queue contention or system load)
**Fix:** Might need to use batch operations or optimize queue implementation

### Scenario B: Slow Event Polling
```
[PERF] _poll_events: 45.67ms (12 events, max event 2.34ms, telemetry 3.12ms) avg10=35.45ms
```

**Diagnosis:** Poll cycle taking 45ms (should be ~2-5ms)
**Cause:** Too many events or slow event dispatch
**Fix:** Could batch events or reduce event frequency

### Scenario C: Slow Event Dispatch
```
[PERF] _dispatch_event CueFinishedEvent: 12.45ms
[PERF] _dispatch_event CueFinishedEvent: 11.78ms
[PERF] _dispatch_event CueFinishedEvent: 13.22ms
```

**Diagnosis:** Finish events taking 12ms each!
**Cause:** Signal handlers (button state updates, UI refreshes) are slow
**Fix:** Might need to optimize SoundFileButton or ButtonBankWidget event handlers

---

## Thresholds

The instrumentation reports slowness when:
- Command methods: `> 5ms`
- Poll events: `> 5ms` AND event_count > 0
- Individual dispatch: `> 5ms`

This is conservative - 5ms is 1/3 of a frame at 60fps, so you'll see warnings for even minor delays.

---

## Next Steps After Collecting Data

1. **Run the test:** `python test_fade_performance.py` or use your normal app
2. **Click "Run: Start 5 Cues"** → Wait for fade to start
3. **Click "Start New Cue During Fade"** → Look for lock-up
4. **Copy all `[PERF]` messages from console** → Share them
5. **Note which operation is slowest** → That's the bottleneck

Once you identify which operation is slow, we can drill down deeper into that specific component.

---

## Advanced: Getting More Detailed Timing

You can lower the threshold in `engine_adapter.py`:

```python
# In __init__:
self._slow_threshold_ms = 1.0  # Show warnings for anything > 1ms
```

This will show more detail but also more noise. Recommended: 2-5ms for diagnosis.

---

## Disabling Instrumentation

When done debugging, set the threshold very high:

```python
self._slow_threshold_ms = 1000.0  # Only show things over 1 second
```

Or completely remove the timing code (will be minimal performance impact).

---

## Example: Complete Test Session Output

```
[14:23:45] Starting 5 cues...
[14:23:45]   Sent play_cue: test_cue_0
[PERF] play_cue took 0.12ms (queue.put: 0.06ms) cue_id=test_cue_0
[14:23:45]   Sent play_cue: test_cue_1
[PERF] play_cue took 0.08ms (queue.put: 0.04ms) cue_id=test_cue_1
[14:23:45]   Sent play_cue: test_cue_2
[PERF] play_cue took 0.09ms (queue.put: 0.05ms) cue_id=test_cue_2
[14:23:45]   Sent play_cue: test_cue_3
[PERF] play_cue took 0.10ms (queue.put: 0.05ms) cue_id=test_cue_3
[14:23:45]   Sent play_cue: test_cue_4
[PERF] play_cue took 0.07ms (queue.put: 0.04ms) cue_id=test_cue_4
[14:23:45] Sent 5 play commands in 0.94ms total
[14:23:46] Fading out 5 cues simultaneously...
[14:23:46]   Sent fade command: test_cue_0
[PERF] fade_cue took 0.11ms (queue.put: 0.05ms) cue_id=test_cue_0 target=-60dB
[14:23:46]   Sent fade command: test_cue_1
[PERF] fade_cue took 0.09ms (queue.put: 0.04ms) cue_id=test_cue_1 target=-60dB
[14:23:46]   Sent fade command: test_cue_2
[PERF] fade_cue took 0.07ms (queue.put: 0.04ms) cue_id=test_cue_2 target=-60dB
[14:23:46]   Sent fade command: test_cue_3
[PERF] fade_cue took 0.08ms (queue.put: 0.05ms) cue_id=test_cue_3 target=-60dB
[14:23:46]   Sent fade command: test_cue_4
[PERF] fade_cue took 0.10ms (queue.put: 0.05ms) cue_id=test_cue_4 target=-60dB
[14:23:46] Sent 5 fade commands in 1.23ms total
[14:23:46] Starting NEW cue while 5 cues are fading out...
[PERF] play_cue took 0.09ms (queue.put: 0.04ms) cue_id=new_cue
[14:23:46] New cue request took 0.18ms
[14:23:46] Test complete. Check console for [PERF] timing data.
```

In this example, all operations are fast (< 1ms), so **the bottleneck is elsewhere** - probably in the audio engine process or in event handling.

---

## Still Slow?

If you see `[PERF]` messages showing slow operations, you've found the bottleneck! Next steps would be:
1. Profile the slow operation (use cProfile)
2. Trace through the code path
3. Apply targeted optimization

If you DON'T see `[PERF]` messages (everything is fast), the block is likely:
- In the audio engine process (needs audio_service.py instrumentation)
- In signal handlers (SoundFileButton, ButtonBankWidget event handlers)
- In Qt main event loop (use Qt Profiler)

