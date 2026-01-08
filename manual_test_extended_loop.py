#!/usr/bin/env python3
"""
Extended loop test: Play WITH loop and let it loop many times to verify stability.
"""

import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand

def main():
    print("Starting AudioEngine...")
    engine = AudioEngine()
    
    test_file = r"C:\Users\Seth Zwiebel\Music\All I Need - Radiohead.mp3"
    if not os.path.exists(test_file):
        print(f"ERROR: Test file not found: {test_file}")
        return
    
    print(f"\n=== EXTENDED LOOP TEST ===")
    print(f"Test file: {test_file}")
    print(f"Will play with loop_enabled=True and let it loop multiple times\n")
    
    # Play WITH loop enabled from the start
    print("[STEP 1] Playing WITH loop enabled...")
    cue_id = "test-extended-loop"
    cmd = PlayCueCommand(
        cue_id=cue_id,
        track_id="test-track",
        file_path=test_file,
        in_frame=0,
        out_frame=None,
        gain_db=0.0,
        fade_in_ms=0,
        loop_enabled=True,  # ENABLE LOOP FROM START
    )
    engine.play_cue(cmd)
    print(f"Play request sent, cue_id={cue_id[:8]}\n")
    
    # Monitor for ~30 seconds
    print("[STEP 2] Monitoring playback for ~30 seconds...\n")
    start_time = time.time()
    loop_count = 0
    last_event = None
    
    while time.time() - start_time < 30.0:
        time.sleep(0.5)
        events = engine.pump()
        
        for evt in events:
            if isinstance(evt, tuple) and len(evt) > 0:
                if evt[0] == "cue_started":
                    print(f"EVENT: cue_started {evt[1][:8]} track={evt[2]}")
                elif evt[0] == "cue_finished":
                    print(f"EVENT: cue_finished {evt[1][:8]} reason={evt[3] if len(evt) > 3 else 'unknown'}")
                    return
                last_event = evt
    
    elapsed = time.time() - start_time
    print(f"\n[RESULT] Test ran for {elapsed:.1f} seconds without cue_finished event")
    print(f"Last event: {last_event}")
    engine.stop()

if __name__ == "__main__":
    main()
