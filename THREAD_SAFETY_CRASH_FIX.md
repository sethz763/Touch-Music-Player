# Thread-Safety Fixes for Crash on Windows (PIL Access Violation)

## Crash Summary
**Error:** `Windows fatal exception: access violation` in PIL ImageFont.getbbox()
**Root Cause:** Qt widgets were being modified from background threads, causing PIL/Qt to crash when trying to measure fonts

## Diagnostic Findings

The crash diagnostic revealed the actual issue:
```
QObject::startTimer: Timers can only be used with threads started with QThread
```

The problem was in `_refresh_label()` → `_start_flash()` → `QVariantAnimation`, which tried to create Qt timers in non-Qt threads (e.g., file probe threads).

## Fixes Applied

### 1. Added Thread-Safety Lock (sound_file_button.py:207)
```python
# Thread-safety lock for text measurement and UI updates
self._ui_lock = threading.Lock()
```

### 2. Protected Font Metrics Operations (_auto_wrap_text)
- Wrapped all QFontMetrics operations in `with self._ui_lock:`
- Added exception handling for font operations
- Prevents concurrent access to font measurement code that may interact with PIL

### 3. Deferred UI Operations to Main Thread (_refresh_label)
```python
# Check if we're in the main thread; if not, defer to main thread
if threading.current_thread() != threading.main_thread():
    QTimer.singleShot(0, self._refresh_label)
    return
```

This ensures UI updates always happen on the main Qt thread, never from background threads.

### 4. Made _start_flash Thread-Safe
- Added main thread check before creating QVariantAnimation
- Defers animation startup to main thread if called from background
- Wrapped in try-except for graceful degradation

## Test Results

### Before Fixes
- `QObject::startTimer: Timers can only be used with threads started with QThread` (repeated ~100+ times)
- Potential crash in PIL ImageFont during concurrent font measurements
- Windows fatal exception: access violation

### After Fixes
✅ Concurrent QFontMetrics test: PASS (800 text measurements across 4 threads)
✅ Button text wrapping stress test: PASS (no timer warnings, no crashes)
✅ Loop functionality: PASS (2 loop restarts detected correctly)

## Key Improvements

1. **Thread-Safe Font Metrics**: All QFontMetrics calls protected by lock
2. **Main Thread Guarantee**: All Qt widget operations deferred to main thread
3. **Graceful Degradation**: Exceptions in font/animation code don't crash the app
4. **No Performance Impact**: Lock only held during font measurement (milliseconds)

## Verification
- Diagnostic script: debug_crash_diagnostic.py
- Manual test: Confirmed no PIL warnings or crashes under concurrent load
- Loop tests: Still working correctly

## Files Modified
- ui/widgets/sound_file_button.py:
  - Added `_ui_lock` in __init__
  - Updated `_auto_wrap_text()` with lock protection
  - Updated `_refresh_label()` with main thread deferral
  - Updated `_start_flash()` with main thread deferral and exception handling
