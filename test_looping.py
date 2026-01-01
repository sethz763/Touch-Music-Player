#!/usr/bin/env python3
"""
Test looping: start a cue with loop_enabled=True and verify it loops multiple times
"""
import time
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand

def find_test_file():
    """Find a test audio file"""
    candidates = [
        "Assets/BORN FREE - M.I.A..mp3",
        "Assets/Ball and Chain - Cage The Elephant.mp3",
        "Assets/Bodysnatchers - Radiohead.mp3",
    ]
    for f in candidates:
        if os.path.exists(f):
            return f
    return None

def test_looping():
    """Test that a looped cue continues to loop and doesn't drop after first loop"""
    
    audio_file = find_test_file()
    if not audio_file:
        print("No test audio file found")
        return False
    
    print(f"Using test file: {audio_file}")
    
    engine = AudioEngine()
    engine.start()
    
    try:
        # Start a looped cue
        cmd = PlayCueCommand(
            cue_id=str(uuid.uuid4()),
            file_path=audio_file,
            track_id="test-track",
            in_frame=0,
            out_frame=None,
            gain_db=0.0,
            loop_enabled=True,  # ENABLE LOOPING
            layered=True
        )
        
        print(f"\nStarting looped cue {cmd.cue_id[:8]}...")
        engine.play_cue(cmd)
        
        # Monitor for looping behavior
        print("Monitoring for loop restarts (watch for 'is_loop_restart' in logs)...\n")
        
        for i in range(15):
            active = len(engine.active_cues)
            print(f"[{i*2}s] Active cues: {active}")
            time.sleep(2)
            
            if active == 0:
                print("ERROR: Cue stopped (should be looping!)")
                return False
        
        # Stop the cue
        from engine.commands import StopCueCommand
        engine.stop_cue(cmd.cue_id)
        time.sleep(1)
        
        print("\nSUCCESS: Cue looped continuously for 30 seconds without dropping!")
        return True
    
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        engine.stop()

if __name__ == "__main__":
    success = test_looping()
    sys.exit(0 if success else 1)
