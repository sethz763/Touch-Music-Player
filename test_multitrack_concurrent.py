#!/usr/bin/env python3
"""
Test multitrack concurrent decoding: start multiple cues in sequence
and verify they all continue playing without interruption.
"""
import time
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand
import uuid

def find_test_file():
    """Find a test audio file"""
    # Try known locations first
    candidates = [
        "Assets/BORN FREE - M.I.A..mp3",
        "Assets/Ball and Chain - Cage The Elephant.mp3",
        "Assets/Bodysnatchers - Radiohead.mp3",
    ]
    for f in candidates:
        if os.path.exists(f):
            return f
    
    # Fallback: search
    for root, dirs, files in os.walk("."):
        for f in files:
            if f.endswith((".mp3", ".wav", ".flac", ".ogg")):
                return os.path.join(root, f)
    return None

def test_multitrack_concurrent():
    """Test that multiple cues can play concurrently without one stopping the other"""
    
    audio_file = find_test_file()
    if not audio_file:
        print("❌ No test audio file found. Please provide an audio file in the project.")
        return False
    
    print(f"Using test file: {audio_file}")
    
    # Create engine
    engine = AudioEngine()
    engine.start()
    
    try:
        # Get file duration
        import av
        container = av.open(audio_file)
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if not stream:
            print("❌ No audio stream found")
            return False
        
        # Calculate duration in seconds
        duration_seconds = stream.duration * stream.time_base if stream.duration else 10
        print(f"Audio duration: {duration_seconds:.2f}s")
        container.close()
        
        # Start multiple cues in sequence (simulating multitrack playback)
        print("\nStarting 4 concurrent cues...")
        cues = []
        
        for i in range(4):
            cmd = PlayCueCommand(
                cue_id=str(uuid.uuid4()),
                file_path=audio_file,
                track_id="test-track",
                in_frame=0,
                out_frame=None,
                gain_db=0.0,
                loop_enabled=False,
                layered=True  # Don't auto-fade, stack playback
            )
            cues.append(cmd)
            engine.play_cue(cmd)
            print(f"  Started cue {i+1} ({cmd.cue_id[:8]})")
            time.sleep(0.5)  # Small delay between starts
        
        print("\nCues started. Monitoring playback for 5 seconds...")
        print("If all cues continue to completion, the multitrack fix is working.\n")
        
        # Monitor for a few seconds
        start_time = time.time()
        initial_state = {}
        
        while time.time() - start_time < 5:
            # Check active cues
            active_cues = set(engine.active_cues.keys())
            
            if not initial_state:
                initial_state = active_cues.copy()
            
            # Print current state
            print(f"[{time.time() - start_time:.1f}s] Active cues: {', '.join(c[:8] for c in sorted(active_cues))}")
            
            time.sleep(1)
        
        # Now wait for cues to finish
        print("\nWaiting for cues to finish (max 30 seconds)...")
        timeout = time.time() + 30
        all_finished = False
        
        while time.time() < timeout:
            active_cues = set(engine.active_cues.keys())
            if not active_cues:
                print("✓ All cues finished naturally!")
                all_finished = True
                break
            print(f"Still active: {', '.join(c[:8] for c in sorted(active_cues))}")
            time.sleep(1)
        
        if not all_finished:
            print("⚠ Cues still active after 30 seconds (might be looping or stuck)")
            for cue_id in engine.active_cues:
                engine.stop_cue(cue_id)
        
        # Summary
        print("\n" + "="*60)
        print("TEST RESULT:")
        print("="*60)
        
        # Check if we had any unexpected removals
        unexpected_removals = []
        for cue_id, reason in engine._removal_reasons.items():
            if cue_id in [c.cue_id for c in cues]:
                # Check if removal reason is one of the 4 allowed ones
                if reason not in ["eof_natural", "manual_stop", "manual_fade", "auto_fade"]:
                    unexpected_removals.append((cue_id, reason))
        
        if unexpected_removals:
            print("❌ FAILED: Cues were removed with unexpected reasons:")
            for cue_id, reason in unexpected_removals:
                print(f"  - {cue_id}: {reason}")
            return False
        else:
            print("✓ PASSED: All cues completed without unexpected removals")
            print("  Removal reasons:")
            for cue_id, reason in engine._removal_reasons.items():
                if cue_id in [c.cue_id for c in cues]:
                    print(f"    - {cue_id[:8]}: {reason}")
            return True
    
    except Exception as e:
        print(f"❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        engine.stop()

if __name__ == "__main__":
    success = test_multitrack_concurrent()
    sys.exit(0 if success else 1)
