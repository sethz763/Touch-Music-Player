#!/usr/bin/env python3
"""
Debug script to verify how engine reports elapsed time during looped playback.
"""

import os
import sys
import time
import numpy as np
from pathlib import Path

# Set debug flag BEFORE importing anything
os.environ['STEPD_TRIMMED_TIME_DEBUG'] = '1'

sys.path.insert(0, str(Path(__file__).parent))

# Import like test_loop_fix.py does
from engine.processes.decode_process_pooled import DecodeStart
from engine.processes.output_process import BufferRequest
from engine.audio_engine import AudioEngine, TransportStop

def create_test_audio(path: str, duration_s: float = 2.0, sr: int = 48000):
    """Create a simple test WAV file."""
    import wave
    n_samples = int(duration_s * sr)
    t = np.linspace(0, duration_s, n_samples, endpoint=False)
    freq = 440
    audio = np.sin(2 * np.pi * freq * t).astype(np.float32)
    audio_int16 = (audio * 32767).astype(np.int16)
    
    with wave.open(path, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(audio_int16.tobytes())
    print(f"Created {path} ({duration_s}s @ {sr}Hz)")

def main():
    audio_path = Path(__file__).parent / "test_time_debug.wav"
    create_test_audio(str(audio_path), duration_s=2.0, sr=48000)
    
    engine = AudioEngine()
    engine.start()
    time.sleep(0.5)
    
    try:
        print("\n=== Testing looped playback time reporting ===")
        # Decode with loop enabled
        decode_cmd = DecodeStart(
            file_path=str(audio_path),
            cue_id="test-loop",
            in_frame=0,
            out_frame=None,
            loop_enabled=True,
            crossfade_out_ms=0,
            start_timestamp=time.time(),
        )
        engine.commands_q.put(decode_cmd)
        time.sleep(0.1)
        
        # Request buffer
        engine.commands_q.put(BufferRequest(cue_id="test-loop", num_frames=48000))
        
        # Capture time events for 2.5 seconds (should see loop restart)
        times_collected = []
        start_time = time.time()
        loop_count = 0
        
        while time.time() - start_time < 2.5:
            try:
                msg = engine.events_q.get(timeout=0.05)
                msg_type = type(msg).__name__
                
                # Look for time events
                if hasattr(msg, 'cue_times'):  # BatchCueTimeEvent
                    for cue_id, (elapsed, remaining) in (msg.cue_times or {}).items():
                        times_collected.append((time.time() - start_time, elapsed, remaining))
                        print(f"  t={time.time()-start_time:.3f}s: {cue_id[:8]} elapsed={elapsed:.4f}s remaining={remaining:.4f}s")
                
                # Check for loop restart in decoder message
                if "[DECODER-EOF-LOOP]" in str(msg) and "successful" in str(msg):
                    loop_count += 1
                    print(f"  *** Loop restart #{loop_count} detected ***")
                    
            except Exception:
                pass
        
        print(f"\nCollected {len(times_collected)} time events")
        if times_collected:
            print("First 5:", times_collected[:5])
            print("Last 5:", times_collected[-5:])
        
        engine.commands_q.put(TransportStop(cue_id="test-loop", fade_out_ms=0))
        
    finally:
        engine.stop()
        audio_path.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
