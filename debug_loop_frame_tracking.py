"""
Diagnostic: Test looping with detailed frame tracking to find the 3-4 loop stopping issue.
"""
import sys
from pathlib import Path

# Add project root
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root))

from engine.cue import CueInfo
from engine.processes.decode_process_pooled import DecoderPoolManager
import numpy as np
import time
import threading

def test_loop_frame_tracking():
    """Test looping with detailed frame tracking."""
    print("=" * 70)
    print("LOOP FRAME TRACKING DIAGNOSTIC")
    print("=" * 70)
    
    # Get test audio file
    test_file = r"C:\Users\Seth Zwiebel\Music\All I Need - Radiohead.mp3"
    
    manager = DecoderPoolManager(num_workers=2)
    
    try:
        # Create a cue with loop enabled and custom out_frame (2 seconds)
        sample_rate = 48000
        duration_s = 2.0
        out_frame = int(sample_rate * duration_s)
        
        cue_info = CueInfo(
            file_path=test_file,
            in_frame=0,
            out_frame=out_frame,
            loop_enabled=True,
            target_sample_rate=sample_rate,
            target_channels=2,
        )
        
        print(f"\nTest Setup:")
        print(f"  File: {test_file}")
        print(f"  Sample rate: {sample_rate}")
        print(f"  Duration: {duration_s}s")
        print(f"  Out frame: {out_frame}")
        print(f"  Loop enabled: True")
        
        cue_id = "test-loop-tracking"
        manager.request_decode(cue_id, cue_info, credit_frames=48000 * 10)  # 10s of credit
        
        total_frames_received = 0
        loop_restarts = 0
        chunks_per_loop = {}
        current_loop = 0
        frames_in_current_loop = 0
        
        print("\nDecoding...")
        for i in range(100):  # Max 100 iterations
            chunk = manager.get_decoded_chunk(timeout=0.5)
            if chunk is None:
                print(f"  [ITER {i}] Timeout - no chunk")
                continue
            
            if chunk.cue_id != cue_id:
                continue
            
            frames = chunk.pcm.shape[0] if chunk.pcm is not None else 0
            total_frames_received += frames
            frames_in_current_loop += frames
            
            if chunk.is_loop_restart:
                loop_restarts += 1
                if current_loop not in chunks_per_loop:
                    chunks_per_loop[current_loop] = []
                chunks_per_loop[current_loop].append(frames_in_current_loop)
                
                print(f"  [LOOP {loop_restarts}] Received {frames_in_current_loop} frames before restart")
                current_loop = loop_restarts
                frames_in_current_loop = 0
            else:
                if current_loop not in chunks_per_loop:
                    chunks_per_loop[current_loop] = []
                chunks_per_loop[current_loop].append(frames)
            
            if chunk.eof:
                print(f"  [EOF] Received {frames} frames, total={total_frames_received}")
                print(f"  [FINAL] Frames in last loop: {frames_in_current_loop}")
                break
            else:
                expected_frames = out_frame
                expected_loops_for_total = total_frames_received // expected_frames
                actual_restart_count = loop_restarts
                
                status = "OK" if expected_loops_for_total >= actual_restart_count else "⚠️ BEHIND"
                print(f"  [ITER {i:2d}] Loop {current_loop} Chunk {frames:5d} frames | Total {total_frames_received:7d} | " + 
                      f"Restarts {loop_restarts} | {status}")
        
        print("\n" + "=" * 70)
        print("SUMMARY:")
        print(f"  Total frames: {total_frames_received}")
        print(f"  Expected per loop: {out_frame}")
        print(f"  Loop restarts: {loop_restarts}")
        print(f"  Frames per loop:")
        for loop_idx, frames_list in sorted(chunks_per_loop.items()):
            total_loop_frames = sum(frames_list)
            print(f"    Loop {loop_idx}: {total_loop_frames} frames ({len(frames_list)} chunks)")
        
        if loop_restarts >= 5:
            print("\n✅ SUCCESS: Looped many times without stopping!")
            return True
        elif loop_restarts >= 3:
            print(f"\n⚠️ WARNING: Only {loop_restarts} loops - user reports stopping at 3-4")
            return False
        else:
            print(f"\n❌ FAILED: Only {loop_restarts} loops")
            return False
            
    finally:
        manager.shutdown()
        print("\n" + "=" * 70)

if __name__ == "__main__":
    success = test_loop_frame_tracking()
    sys.exit(0 if success else 1)
