# Audio Stuttering Fix - Debug Logging Performance

## Problem
Audio was stuttering when using short looping clips in layered (multitrack) mode with WAV files.

## Root Cause
**Verbose debug logging** was causing performance bottlenecks:

1. **Decoder chunk logging**: Every decoded chunk (4-4.5KB at 48kHz, ~20ms) printed a debug message
   - With short clips, chunks arrive ~50 times per second
   - With 4 concurrent cues: 200+ messages per second

2. **BufferRequest logging**: Every buffer request from output process printed a debug message
   - Similar frequency to chunk logging

3. **Queue overhead**: Each debug message went through multiprocessing Queue to engine for logging
   - Queue operations are not free - they involve locks and scheduling
   - With 200+ messages/sec, this starves the audio threads

4. **Real-time impact**: Audio processing requires consistent low-latency execution
   - Logging overhead causes scheduling delays
   - Output buffers underrun when decoder/output threads are blocked on logging

## Solution
Commented out the most frequent debug statements:

1. **Decoder chunk logging** - Removed per-chunk print statements from `_decode_worker_pool()`
   - Now only logs on critical events (errors, worker startup)
   
2. **BufferRequest logging** - Removed per-request print statements
   - These were called dozens of times per second per cue

3. **Preserved critical logging** - Kept logging for:
   - Worker startup/shutdown
   - Decode errors
   - Critical state changes

## Files Modified
- `engine/processes/decode_process_pooled.py`
  - Lines ~280: Removed chunk logging (kept EOF logging)
  - Lines ~157: Removed BufferRequest logging

## Performance Impact
- **Before**: 200+ debug messages per second with 4 looping clips
- **After**: <5 debug messages per second (only critical events)

## Trade-offs
- **Pro**: Eliminates stuttering, better real-time performance
- **Con**: Less detailed logging during normal operation
- **Workaround**: Debug logging can be re-enabled by uncommenting the print statements for troubleshooting

## How to Re-enable Debug Logging
If you need detailed logs for troubleshooting, uncomment the DEBUG comment lines in:
```python
# DEBUG: Uncomment for buffer requests: print(f"...")
# DEBUG: Uncomment for chunks: print(f"...")
```

But be aware this will significantly impact performance with multiple concurrent cues.

## Recommendation
This is a good example of why performance-critical code should have **conditional logging** that can be toggled at runtime without code changes, using something like a log level configuration.
