"""
Debug script to trace exact sequence of events during fade-out + new cue start.
Logs all engine commands and output process events with precise timestamps.
"""

import time
import sys
from pathlib import Path
from engine.audio_engine import AudioEngine
from engine.commands import PlayCueCommand
from engine.messages.events import CueStartedEvent, CueFinishedEvent

def format_timestamp():
    return time.time()

def log_event(event_type: str, cue_id: str = None, message: str = None, data: dict = None):
    ts = format_timestamp()
    parts = [f"[{ts:.3f}]", f"[{event_type}]"]
    if cue_id:
        parts.append(f"cue={cue_id[:8]}")
    if message:
        parts.append(message)
    if data:
        parts.append(str(data))
    print(" ".join(parts))

def main():
    print("[TEST] Starting fade transition debug test")
    
    # Create engine with standard settings
    engine = AudioEngine(
        sample_rate=48000,
        channels=2,
        block_frames=2048,
        fade_in_ms=100,
        fade_out_ms=1000,  # 1 second fade
        fade_curve="equal_power",
        auto_fade_on_new=True,
    )
    
    # Start engine processes
    engine.start()
    log_event("ENGINE", message="Engine started")
    
    try:
        # Wait for engine to be ready
        time.sleep(0.5)
        
        # File paths for testing
        file1 = "C:/Users/Seth Zwiebel/Music/Faust Arp - Radiohead (1).mp3"
        file2 = "C:/Users/Seth Zwiebel/Music/Everything In Its Right Place - Radiohead.mp3"
        
        # === PHASE 1: Play first cue ===
        print("\n" + "="*80)
        print("[PHASE 1] Playing first cue")
        print("="*80)
        
        cmd1 = PlayCueCommand(
            cue_id="cue_001",
            file_path=file1,
            in_frame=0,
            out_frame=None,
            gain_db=-6.0,
            loop_enabled=False,
        )
        log_event("PLAY", cue_id=cmd1.cue_id, message=f"file={Path(file1).name}")
        engine.play_cue(cmd1)
        
        # Let first cue play and buffer
        print("\n[WAIT] Waiting 3 seconds for first cue to establish...")
        for i in range(3):
            events = engine.pump()
            for evt in events:
                if isinstance(evt, CueStartedEvent):
                    log_event("EVENT", cue_id=evt.cue_id, message="CueStartedEvent")
                elif isinstance(evt, CueFinishedEvent):
                    log_event("EVENT", cue_id=evt.cue_info.cue_id, message=f"CueFinishedEvent reason={evt.reason}")
            time.sleep(1.0)
        
        # === PHASE 2: Start new cue while first is playing ===
        print("\n" + "="*80)
        print("[PHASE 2] Starting new cue (should trigger auto-fade of first)")
        print("="*80)
        
        cmd2 = PlayCueCommand(
            cue_id="cue_002",
            file_path=file2,
            in_frame=0,
            out_frame=None,
            gain_db=-6.0,
            loop_enabled=False,
        )
        log_event("PLAY", cue_id=cmd2.cue_id, message=f"file={Path(file2).name}")
        start_time = format_timestamp()
        engine.play_cue(cmd2)
        
        # Pump events and track what happens
        print("\n[MONITOR] Tracking events during transition (next 5 seconds)...")
        transition_start = format_timestamp()
        active_cues = set()
        
        while format_timestamp() - transition_start < 5.0:
            events = engine.pump()
            
            for evt in events:
                if isinstance(evt, CueStartedEvent):
                    active_cues.add(evt.cue_id)
                    log_event("EVENT", cue_id=evt.cue_id, message="CueStartedEvent", data={"active_cues": len(active_cues)})
                elif isinstance(evt, CueFinishedEvent):
                    active_cues.discard(evt.cue_info.cue_id)
                    log_event("EVENT", cue_id=evt.cue_info.cue_id, message=f"CueFinishedEvent", data={"reason": evt.reason, "active_cues": len(active_cues)})
            
            # Also log engine state
            if events:
                elapsed = format_timestamp() - start_time
                print(f"  [STATE] elapsed={elapsed:.3f}s active_cues={list(engine.active_cues.keys())}")
            
            time.sleep(0.1)
        
        # === PHASE 3: Wait for second cue to play ===
        print("\n" + "="*80)
        print("[PHASE 3] Waiting to hear second cue play...")
        print("="*80)
        
        print("\n[WAIT] Monitoring for 5 more seconds...")
        monitor_start = format_timestamp()
        
        while format_timestamp() - monitor_start < 5.0:
            events = engine.pump()
            
            for evt in events:
                if isinstance(evt, CueStartedEvent):
                    active_cues.add(evt.cue_id)
                    log_event("EVENT", cue_id=evt.cue_id, message="CueStartedEvent", data={"active_cues": len(active_cues)})
                elif isinstance(evt, CueFinishedEvent):
                    active_cues.discard(evt.cue_info.cue_id)
                    log_event("EVENT", cue_id=evt.cue_info.cue_id, message=f"CueFinishedEvent reason={evt.reason}", data={"active_cues": len(active_cues)})
            
            elapsed = format_timestamp() - start_time
            print(f"  [STATE] elapsed={elapsed:.3f}s active_cues={len(active_cues)} ids={[c[:8] for c in list(engine.active_cues.keys())]}")
            
            time.sleep(0.2)
        
        print("\n[TEST] Completed")
        
    finally:
        engine.stop()
        log_event("ENGINE", message="Engine stopped")

if __name__ == "__main__":
    main()
