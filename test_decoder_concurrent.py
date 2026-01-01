#!/usr/bin/env python3
"""Test the pooled decoder with multiple concurrent cues"""
import multiprocessing as mp
import time
import random
from engine.processes.decode_process_pooled import (
    decode_process_main, DecodeStart, BufferRequest, DecodeStop
)
from engine.messages.events import CueFinishedEvent

def test_concurrent_decode():
    """Simulate multiple cues being decoded concurrently"""
    mp.set_start_method('spawn', force=True)
    
    cmd_q = mp.Queue()
    out_q = mp.Queue()
    event_q = mp.Queue()
    
    # Start coordinator in background
    coordinator = mp.Process(target=decode_process_main, args=(cmd_q, out_q, event_q))
    coordinator.daemon = False
    coordinator.start()
    
    time.sleep(1)  # Let coordinator start workers
    
    # Test files (use first 10 seconds of each)
    files = [
        ("C:/Users/Seth Zwiebel/Music/X15 Step.mp3", 10 * 44100),
        ("C:/Users/Seth Zwiebel/Music/All I Need - Radiohead.mp3", 10 * 44100),
        ("C:/Users/Seth Zwiebel/Music/American Life - Primus.mp3", 10 * 44100),
    ]
    
    cue_ids = []
    
    # Start 8 cues concurrently
    for i in range(8):
        file_path, duration_frames = random.choice(files)
        cue_id = f"test-cue-{i:02d}"
        cue_ids.append(cue_id)
        
        print(f"[TEST] Starting cue {cue_id}")
        cmd_q.put(DecodeStart(
            cue_id=cue_id,
            track_id=f"track-{i}",
            file_path=file_path,
            in_frame=0,
            out_frame=duration_frames,
            gain_db=0.0,
            loop_enabled=False,
            target_sample_rate=44100,
            target_channels=2,
            block_frames=4096
        ))
        
        # Request initial buffer
        cmd_q.put(BufferRequest(cue_id=cue_id, frames_needed=16384))
        time.sleep(0.1)
    
    # Let them play for a bit and monitor
    finished_cues = set()
    total_frames_received = {cue_id: 0 for cue_id in cue_ids}
    
    print("\n[TEST] Monitoring cue playback...")
    start_time = time.time()
    
    while time.time() - start_time < 30:  # Run for 30 seconds
        # Check output queue for decoded chunks and events
        while True:
            try:
                msg = out_q.get_nowait()
            except Exception:
                break
            
            if hasattr(msg, 'cue_id'):
                if hasattr(msg, 'pcm'):
                    # DecodedChunk
                    cue_id = msg.cue_id[:8]
                    frames = msg.pcm.shape[0]
                    eof = msg.eof
                    total_frames_received[msg.cue_id] = total_frames_received.get(msg.cue_id, 0) + frames
                    
                    print(f"[CHUNK] {cue_id}: {frames} frames, eof={eof}, total={total_frames_received[msg.cue_id]}")
                    
                    if eof:
                        finished_cues.add(msg.cue_id)
        
        # Request more buffers from active cues
        for cue_id in cue_ids:
            if cue_id not in finished_cues:
                try:
                    cmd_q.put(BufferRequest(cue_id=cue_id, frames_needed=8192))
                except Exception:
                    pass
        
        time.sleep(0.5)
    
    # Print summary
    print("\n[TEST] Summary:")
    print(f"Started: {len(cue_ids)} cues")
    print(f"Finished: {len(finished_cues)} cues")
    for cue_id in cue_ids:
        frames = total_frames_received.get(cue_id, 0)
        seconds = frames / 44100.0
        print(f"  {cue_id[:8]}: {frames} frames ({seconds:.2f}s)")
    
    # Cleanup
    cmd_q.put(None)
    coordinator.join(timeout=5)
    if coordinator.is_alive():
        coordinator.terminate()

if __name__ == "__main__":
    test_concurrent_decode()
