#!/usr/bin/env python3
"""
Test script to verify the pre-buffering optimization for loop restarts.
This runs without Qt GUI - just tests decode process looping performance.
"""
import sys
import time
import numpy as np
import av
import queue
from engine.processes.decode_process import decode_process_main, DecodeStart, BufferRequest
import multiprocessing as mp

def test_prebuffering_loop():
    """Test that pre-buffering speeds up loop restarts."""
    # Use the existing dog barking audio file
    file_path = r"C:\Users\Seth Zwiebel\OneDrive\Documents\GPI on air light\sfx\mixkit-dog-barking-twice-1.wav"
    
    # Create queues for IPC
    cmd_q = mp.Queue()
    out_q = mp.Queue()
    evt_q = mp.Queue()
    
    # Start decode process
    decode_proc = mp.Process(target=decode_process_main, args=(cmd_q, out_q, evt_q), daemon=False)
    decode_proc.start()
    
    try:
        cue_id = "test-cue-prebuffer"
        track_id = "track-001"
        
        # Send DecodeStart with loop enabled
        start_msg = DecodeStart(
            cue_id=cue_id,
            track_id=track_id,
            file_path=file_path,
            in_frame=0,
            out_frame=None,
            gain_db=0.0,
            loop_enabled=True,
            target_sample_rate=48000,
            target_channels=2,
            block_frames=2048,
        )
        cmd_q.put(start_msg)
        print("[TEST] Sent DecodeStart with loop_enabled=True")
        
        # Track timing for each loop iteration
        loop_timings = {}
        total_chunks = 0
        loop_iteration = 0
        first_chunk_received = False
        
        print("\n" + "="*70)
        print("MONITORING LOOP ITERATIONS AND PRE-BUFFERING")
        print("="*70 + "\n")
        
        start_time = time.time()
        timeout = 15  # seconds
        
        while time.time() - start_time < timeout:
            # Check for output
            try:
                msg = out_q.get(timeout=0.1)
                
                # Check for events first
                if isinstance(msg, tuple):
                    if msg[0] == "started":
                        print(f"[EVENT] Decoder started: {msg[1]}")
                    elif msg[0] == "looped":
                        loop_iteration += 1
                        loop_timings[loop_iteration] = {
                            "restart_time": time.time(),
                            "chunks_received": 0
                        }
                        print(f"\n[LOOP-{loop_iteration}] Loop restart event received at {time.time():.3f}")
                    continue
                
                # Handle DecodedChunk
                if hasattr(msg, 'pcm'):
                    total_chunks += 1
                    frames = msg.pcm.shape[0] if msg.pcm.size > 0 else 0
                    
                    if not first_chunk_received:
                        print(f"[FIRST-CHUNK] Received first chunk: {frames} frames, EOF={msg.eof}")
                        first_chunk_received = True
                        # Send first BufferRequest
                        cmd_q.put(BufferRequest(cue_id, 8192))
                        print(f"[BUFFER-REQUEST] Sent initial BufferRequest(8192 frames)")
                    
                    if loop_iteration > 0 and loop_iteration in loop_timings:
                        loop_timings[loop_iteration]["chunks_received"] += 1
                        elapsed_since_restart = time.time() - loop_timings[loop_iteration]["restart_time"]
                        print(f"[LOOP-{loop_iteration}-CHUNK] Chunk #{loop_timings[loop_iteration]['chunks_received']}: "
                              f"{frames} frames (elapsed since restart: {elapsed_since_restart:.4f}s), EOF={msg.eof}")
                    
                    if msg.eof:
                        print(f"[EOF] DecodedChunk with eof=True (loop_iteration={loop_iteration})")
                    
                    # Send follow-up buffer request to keep decoder going
                    if total_chunks % 5 == 0:
                        cmd_q.put(BufferRequest(cue_id, 4096))
                    
                    # Stop after 3 complete loops
                    if loop_iteration >= 3:
                        print(f"\n[SUCCESS] Got {loop_iteration} complete loop restarts!")
                        break
                        
            except queue.Empty:
                # Normal: no decoded chunk available within timeout.
                continue
            except Exception as e:
                print(f"[ERROR] Queue error: {type(e).__name__}: {e}")
                if not decode_proc.is_alive():
                    break
        
        # Print summary
        print("\n" + "="*70)
        print("PRE-BUFFERING PERFORMANCE SUMMARY")
        print("="*70)
        
        if loop_timings:
            for loop_num in sorted(loop_timings.keys()):
                info = loop_timings[loop_num]
                print(f"Loop {loop_num}: {info['chunks_received']} chunks received after restart")
        
        total_time = time.time() - start_time
        print(f"\nTotal test duration: {total_time:.2f}s")
        print(f"Total chunks processed: {total_chunks}")
        
    finally:
        # Cleanup
        decode_proc.terminate()
        decode_proc.join(timeout=2)
        if decode_proc.is_alive():
            decode_proc.kill()
            decode_proc.join()

if __name__ == "__main__":
    print("Testing pre-buffering optimization...\n")
    test_prebuffering_loop()
