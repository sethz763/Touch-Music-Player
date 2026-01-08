#!/usr/bin/env python3
"""
Test scenario: Play WITHOUT loop, then toggle loop ON while playing.
This reproduces the user's exact scenario that fails after 5 loops.
"""

import time
import sys
import os

# Add project to path
sys.path.insert(0, os.path.dirname(__file__))

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand, UpdateCueCommand

def main():
    print("Starting AudioEngine...")
    engine = AudioEngine()
    
    # Find a test music file
    test_file = r"C:\Users\Seth Zwiebel\Music\All I Need - Radiohead.mp3"
    if not os.path.exists(test_file):
        print(f"ERROR: Test file not found: {test_file}")
        return
    
    print(f"\n=== SCENARIO: Toggle loop ON while playing ===")
    print(f"Test file: {test_file}")
    
    # Step 1: Play WITHOUT loop
    print("\n[STEP 1] Playing without loop...")
    cue_id = "test-cue-loop-toggle"
    cmd = PlayCueCommand(
        cue_id=cue_id,
        track_id="test-track",
        file_path=test_file,
        in_frame=0,
        out_frame=None,
        gain_db=0.0,
        fade_in_ms=0,
        loop_enabled=False,  # START WITHOUT LOOP
    )
    engine.play_cue(cmd)
    print(f"Play request sent, cue_id={cue_id[:8]}")
    
    # Wait for playback to start
    print("\n[STEP 2] Waiting for playback to start...")
    time.sleep(1.0)
    
    # Step 3: Toggle loop ON while playing
    print("\n[STEP 3] Toggling loop ON while cue is playing...")
    for iteration in range(1, 10):
        print(f"\n  [Iteration {iteration}] Waiting 2 seconds before pump...")
        time.sleep(2.0)
        
        # Pump the engine to get events
        events = engine.pump()
        for evt in events:
            if isinstance(evt, tuple) and len(evt) > 0:
                if evt[0] == "cue_started":
                    print(f"    EVENT: cue_started {evt[1][:8]}")
                elif evt[0] == "cue_finished":
                    print(f"    EVENT: cue_finished {evt[1][:8]} reason={evt[3]}")
                    return  # Test ended
        
        # On first iteration, toggle loop to ON
        if iteration == 1:
            print(f"\n  [Iteration {iteration}] TOGGLING loop to ON...")
            update_cmd = UpdateCueCommand(
                cue_id=cue_id,
                loop_enabled=True,  # ENABLE LOOP
            )
            engine.update_cue(update_cmd)
            print(f"  Loop toggle sent")
    
    print("\n[DONE] Test completed (no finish event received)")
    engine.stop()

if __name__ == "__main__":
    main()
