#!/usr/bin/env python3
"""
Test script to exercise and debug auto-fade logic.
Simulates multiple cues playing then switching to auto-fade mode.
"""
import sys
import os
import time
import queue

# Add the project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.audio_engine import AudioEngine
from engine.track import Track
from engine.commands import PlayCueCommand

def test_autofade():
    """Test auto-fade with multiple cues"""
    print("\n" + "="*60)
    print("AUTO-FADE TEST: Start multiple cues then enable auto-fade")
    print("="*60 + "\n")
    
    # Create engine
    engine = AudioEngine(sample_rate=48000, channels=2, block_frames=512)
    engine.start()
    
    # No need to setLevel on engine.log
    
    time.sleep(1)  # Let engine start
    
    # Create some test tracks
    tracks = []
    music_dir = os.path.expanduser("~/Music")
    music_files = [
        os.path.join(music_dir, "Hats Off - Primus.mp3"),
        os.path.join(music_dir, "X15 Step.mp3"),
        os.path.join(music_dir, "All I Need - Radiohead.mp3"),
        os.path.join(music_dir, "American Life - Primus.mp3"),
    ]
    
    # Filter to existing files
    existing_files = [f for f in music_files if os.path.exists(f)]
    
    if not existing_files:
        print("ERROR: No music files found in ~/Music")
        engine.stop()
        return
    
    print(f"\nFound {len(existing_files)} music files to test with:")
    for f in existing_files:
        print(f"  - {os.path.basename(f)}")
    
    # Play first 3-4 cues
    num_cues = min(4, len(existing_files))
    print(f"\n[PHASE 1] Playing {num_cues} cues simultaneously...")
    
    cue_ids = []
    for i in range(num_cues):
        file_path = existing_files[i]
        
        cmd = PlayCueCommand(
            cue_id=f"test_cue_{i}",
            file_path=file_path,
            in_frame=0,
            gain_db=0.0,
            loop_enabled=False,
            fade_in_ms=100,
            layered=True  # Start as layered (no auto-fade)
        )
        
        print(f"  Playing cue {i+1}: {os.path.basename(file_path)}")
        engine.handle_command(cmd)
        time.sleep(0.1)  # Stagger the starts slightly
    
    print(f"\n[PHASE 1 COMPLETE] {num_cues} cues are now playing in layered mode\n")
    time.sleep(2)  # Let them play for a bit
    
    # Now switch to auto-fade mode
    print("[PHASE 2] Enabling auto-fade mode...")
    engine.set_auto_fade_on_new(True)
    print(f"Auto-fade enabled: {engine.get_auto_fade_on_new()}\n")
    
    # Play one more cue with auto-fade enabled
    print("[PHASE 3] Playing new cue with auto-fade (should fade out others)...")
    new_file = existing_files[(num_cues) % len(existing_files)]
    
    new_cmd = PlayCueCommand(
        cue_id="test_cue_new",
        file_path=new_file,
        in_frame=0,
        gain_db=0.0,
        loop_enabled=False,
        fade_in_ms=100,
        layered=False  # Now with auto-fade enabled
    )
    
    print(f"  Playing new cue: {os.path.basename(new_file)}")
    engine.handle_command(new_cmd)
    
    print("\n[MONITORING] Watching for fade completion...\n")
    
    # Monitor for 5 seconds
    start_time = time.time()
    while time.time() - start_time < 5:
        # Let engine process events
        try:
            events = engine.pump()
        except Exception as e:
            print(f"[ERROR] pump() failed: {e}")
            break
        time.sleep(0.05)  # 50ms between pumps
    
    print("\n[TEST COMPLETE]\n")
    
    # Cleanup
    engine.stop()
    print("Engine stopped.\n")

if __name__ == "__main__":
    test_autofade()
