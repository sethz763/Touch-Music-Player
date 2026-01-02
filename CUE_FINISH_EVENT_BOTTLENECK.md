# GUI Blocking During Cue Transitions: Root Cause Analysis

## Primary Bottleneck: Signal Broadcasting to All Buttons

### The Problem

Each button is subscribed **directly** to the EngineAdapter's signals:

**Current Architecture (Problematic):**
```
EngineAdapter emits cue_finished
    ↓
All 24 buttons receive signal (24 Qt signal slots fire)
    ↓
Each button filters by _active_cue_ids
    ↓
24 * N signal processing operations
```

When 5 cues finish simultaneously:
- `cue_finished` signal emitted 5 times
- Each emission triggers 24 button handlers
- Total: 5 * 24 = **120 signal handler invocations** just to finish 5 cues

### Code Location

**[ui/widgets/button_bank_widget.py](ui/widgets/button_bank_widget.py#L175-L179)**
```python
def set_engine_adapter(self, engine_adapter: EngineAdapter) -> None:
    for btn in self.buttons:
        btn.subscribe_to_adapter(engine_adapter)  # Each button subscribes to ALL signals
```

**[ui/widgets/sound_file_button.py](ui/widgets/sound_file_button.py#L307-L312)**
```python
def _subscribe_to_adapter(self, adapter: EngineAdapter) -> None:
    adapter.cue_started.connect(self._on_cue_started)
    adapter.cue_finished.connect(self._on_cue_finished)  # ← Gets 24 copies
    adapter.cue_time.connect(self._on_cue_time)         # ← Gets 24 copies
    adapter.cue_levels.connect(self._on_cue_levels)     # ← Gets 24 copies
```

## Secondary Issues

### 1. No Event Batching for Finish Events
Currently each cue finish is a separate signal emission:
```
Cue 1 finishes → emit cue_finished
Cue 2 finishes → emit cue_finished
Cue 3 finishes → emit cue_finished
...
```

Should batch multiple finishes into one signal (like BatchCueLevelsEvent).

### 2. Qt Signal Overhead
Each signal emission has overhead:
- Method lookup in Qt's meta-object system
- Slot queue in event loop
- Stack frame creation
- Parameter marshalling

With 24 buttons and 5 cues finishing: 120 separate operations.

### 3. No Short-Circuit in Button Filter
Every button processes every event even if cue_id doesn't match:
```python
def _on_cue_finished(self, cue_id: str, cue_info: object, reason: str) -> None:
    if cue_id not in self._active_cue_ids:
        return  # ← Still had to go through Qt signal dispatch to get here
```

## Solution Strategy

### Phase 1: Centralized Event Routing (Critical)

Instead of broadcasting to all buttons, have ButtonBankWidget receive events and route to correct button:

```
EngineAdapter emits cue_finished
    ↓
ButtonBankWidget._on_cue_finished() (1 signal handler)
    ↓
Find button that owns cue_id
    ↓
Call button._on_cue_finished_direct() directly (no Qt signal)
    ↓
Total: 5 button method calls (not 5 * 24)
```

**Impact:** 80% reduction in signal overhead (24x fewer invocations)

### Phase 2: Event Batching for Finishes (Medium)

Create `BatchCueFinishedEvent` similar to existing `BatchCueLevelsEvent`:

```python
@dataclass
class BatchCueFinishedEvent:
    cue_finishes: list  # [(cue_id, cue_info, reason), ...]
```

This would:
- Reduce queue traversals from N to 1 for N finishes
- Allow buttons to batch update their UI
- Single event loop cycle for multiple finishes

**Impact:** Further 20-50% reduction in UI thrashing

### Phase 3: Lazy Signal Filtering (Optional)

Use Qt's `QSignalBlocker` or custom guards to prevent unnecessary emissions:

```python
# Don't even create the signal if button doesn't own the cue
if cue_id not in self._active_cue_ids:
    return  # Before any Qt machinery fires
```

**Impact:** Minimal signal creation overhead

## Implementation Priority

| Issue | Severity | Effort | Impact | Priority |
|-------|----------|--------|--------|----------|
| Broadcasting to all buttons | CRITICAL | MEDIUM | 80% overhead reduction | **NOW** |
| Event batching for finishes | HIGH | MEDIUM | 20-50% additional reduction | **SOON** |
| Lazy filtering | LOW | LOW | 5-10% reduction | **LATER** |

## Expected Performance Impact

### Current (Broadcasting)
```
5 cues finish simultaneously
→ 5 cue_finished signals emitted
→ Each hits 24 button handlers
→ ~120 Qt signal invocations
→ 10-50ms GUI lag during transition
```

### After Phase 1 (Centralized Routing)
```
5 cues finish simultaneously
→ 5 cue_finished signals emitted
→ ButtonBankWidget receives and routes to 5 buttons directly
→ ~5 method calls (no Qt signal overhead)
→ <5ms GUI lag
```

### After Phase 2 (Batched Events)
```
5 cues finish simultaneously
→ 1 batch_cue_finished signal emitted
→ ButtonBankWidget processes batch for 5 buttons
→ All UI updates in single event loop cycle
→ <2ms GUI lag
```

## Files to Modify

1. **gui/engine_adapter.py**
   - Add `cue_finished_batch` signal (optional for Phase 2)
   - Still emit individual signals for backward compat

2. **engine/messages/events.py**
   - Add `BatchCueFinishedEvent` class (Phase 2)

3. **ui/widgets/button_bank_widget.py** (Primary)
   - Subscribe ONLY ButtonBankWidget to adapter signals
   - Implement routing methods for each event type
   - Call button methods directly (not via signals)

4. **ui/widgets/sound_file_button.py**
   - Convert signal handlers to direct methods
   - Keep internal state consistent
   - Add `_on_cue_finished_direct()` style methods

## Testing Checklist

- [ ] Single cue finish: <10ms GUI response
- [ ] Multiple cue finish (5): <20ms GUI response
- [ ] Auto-fade transition (new cue while fading old): <30ms
- [ ] CPU usage lower or equal
- [ ] No missed events
- [ ] Button state stays consistent
