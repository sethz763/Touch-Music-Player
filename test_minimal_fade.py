#!/usr/bin/env python3
"""
Minimal test for fade-out + new cue transition.
Directly tests the audio_service without GUI.
"""

import multiprocessing as mp
import time
from pathlib import Path
from engine.audio_service import audio_service_main, AudioServiceConfig
from engine.commands import PlayCueCommand

def test_fade_transition():
    """Test playing a cue, then fading it out while playing a new one."""
    
    # Create queues
    ctx = mp.get_context("spawn")
    cmd_q = ctx.Queue()
    evt_q = ctx.Queue()
    
    # Audio config with 1-second fade
    config = AudioServiceConfig(
        sample_rate=48000,
        channels=2,
        block_frames=2048,
        fade_in_ms=100,
        fade_out_ms=1000,
        fade_curve="equal_power",
        auto_fade_on_new=True,
    )
    
    # Start audio service
    service = mp.Process(
        target=audio_service_main,
        args=(cmd_q, evt_q, config),
        daemon=False,
    )
    service.start()
    
    try:
        print("[TEST] Audio service started")
        time.sleep(0.5)
        
        # Files
        file1 = "C:/Users/Seth Zwiebel/Music/Faust Arp - Radiohead (1).mp3"
        file2 = "C:/Users/Seth Zwiebel/Music/Everything In Its Right Place - Radiohead.mp3"
        
        # === PHASE 1: Play first cue ===
        print("\n[PHASE 1] Playing first cue...")
        cmd1 = PlayCueCommand(
            cue_id="cue_001",
            file_path=file1,
            in_frame=0,
            out_frame=None,
            gain_db=-6.0,
            loop_enabled=False,
        )
        cmd_q.put(cmd1)
        
        # Wait for it to start
        print("[WAIT] Waiting 2 seconds for first cue to buffer...")
        time.sleep(2.0)
        
        # Drain any pending events
        while not evt_q.empty():
            try:
                evt = evt_q.get_nowait()
                print(f"  Event: {evt}")
            except:
                break
        
        # === PHASE 2: Play new cue (should auto-fade first) ===
        print("\n[PHASE 2] Starting new cue (should trigger auto-fade)...")
        cmd2 = PlayCueCommand(
            cue_id="cue_002",
            file_path=file2,
            in_frame=0,
            out_frame=None,
            gain_db=-6.0,
            loop_enabled=False,
        )
        cmd_q.put(cmd2)
        
        # Monitor next 5 seconds
        print("[MONITOR] Tracking events during transition...")
        start_time = time.time()
        event_count = 0
        
        while time.time() - start_time < 5.0:
            try:
                evt = evt_q.get(timeout=0.1)
                event_count += 1
                elapsed = time.time() - start_time
                if isinstance(evt, tuple) and evt[0] == "debug":
                    print(f"  [{elapsed:.2f}s] DEBUG: {evt[1]}")
                elif isinstance(evt, tuple) and evt[0] == "finished":
                    print(f"  [{elapsed:.2f}s] EVENT: cue_finished cue={evt[1][:8]}")
                else:
                    print(f"  [{elapsed:.2f}s] EVENT: {type(evt).__name__}")
            except:
                pass
            time.sleep(0.01)
        
        print(f"\n[SUMMARY] Received {event_count} events during transition")
        
    finally:
        # Cleanup
        print("\n[CLEANUP] Stopping audio service...")
        cmd_q.put(None)
        service.join(timeout=2.0)
        if service.is_alive():
            service.terminate()
            service.join()
        print("[CLEANUP] Done")

if __name__ == "__main__":
    test_fade_transition()
