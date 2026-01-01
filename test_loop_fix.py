#!/usr/bin/env python3
"""
Simple test script to verify the looping fix works.
Creates a short synthetic audio file and tests looping.
"""
import os
import sys
import time
import numpy as np
import av

def create_test_audio(filename, duration_seconds=2, sample_rate=44100):
    """Create a short test audio file with a simple tone."""
    print(f"Creating test audio: {filename}")
    
    # Create a simple sine wave (440 Hz, standard A note)
    t = np.linspace(0, duration_seconds, int(sample_rate * duration_seconds), False)
    frequency = 440  # Hz
    audio_data = np.sin(2 * np.pi * frequency * t).astype(np.float32)
    
    # Use PyAV to write the audio
    container = av.open(filename, 'w')
    stream = container.add_stream('pcm_s16le', rate=sample_rate)
    
    # Convert float32 [-1, 1] to int16
    audio_int16 = (audio_data * 32767).astype(np.int16)
    
    # Create frame
    frame = av.AudioFrame.from_ndarray(audio_int16.reshape(-1, 1), format='s16', layout='mono')
    frame.sample_rate = sample_rate
    
    # Write the frame
    for packet in stream.encode(frame):
        container.mux(packet)
    
    # Flush remaining packets
    for packet in stream.encode():
        container.mux(packet)
    
    container.close()
    print(f"Created {filename} ({duration_seconds}s @ {sample_rate}Hz)")

def test_decode_looping():
    """Test that the decode process can handle looping correctly."""
    from engine.processes.decode_process import decode_process_main, DecodeStart, BufferRequest
    import multiprocessing as mp
    from queue import Queue
    
    # Create test audio file
    test_file = "test_audio.wav"
    if not os.path.exists(test_file):
        create_test_audio(test_file, duration_seconds=1)
    
    # Setup queues
    cmd_q = mp.Queue()
    out_q = mp.Queue()
    
    # Start decode process
    decode_proc = mp.Process(
        target=decode_process_main,
        args=(cmd_q, out_q),
        daemon=True
    )
    decode_proc.start()
    
    try:
        # Send decode start command with looping
        cue_id = "test_cue_1"
        start_msg = DecodeStart(
            cue_id=cue_id,
            track_id="track_1",
            file_path=os.path.abspath(test_file),
            target_sample_rate=44100,
            target_channels=2,
            loop_enabled=True,
            in_frame=0,
            out_frame=None,
            block_frames=4096,
        )
        cmd_q.put(start_msg)
        print("Sent DecodeStart with loop_enabled=True")
        
        # Simulate output process sending buffer requests
        total_chunks = 0
        loop_count = 0
        start_time = time.time()
        timeout = 10  # seconds
        
        while time.time() - start_time < timeout:
            # Check for output
            try:
                msg = out_q.get(timeout=0.1)
                print(f"Got message: {type(msg).__name__}")
                
                if hasattr(msg, 'eof'):
                    print(f"  EOF: {msg.eof}, frames: {msg.pcm.shape[0] if hasattr(msg, 'pcm') else 'N/A'}")
                    if msg.eof and loop_count == 0:
                        print("First loop completed!")
                        loop_count += 1
                        total_chunks = 0
                    elif msg.eof:
                        loop_count += 1
                        print(f"Loop {loop_count} completed!")
                else:
                    print(f"  {msg}")
                
                total_chunks += 1
                
                if loop_count >= 2:
                    print(f"\nSUCCESS: Looping works! Got {loop_count} complete loops")
                    break
                
                # Send buffer request to keep decoder going
                if total_chunks % 5 == 0:
                    cmd_q.put(BufferRequest(cue_id, 4096))
                    print(f"Sent BufferRequest")
                    
            except Exception as e:
                if "Empty" not in str(e):
                    print(f"Queue error: {e}")
        
        if loop_count < 2:
            print(f"\nWARNING: Only got {loop_count} loops in {timeout}s")
            print("Checking for deadlock...")
        
    finally:
        decode_proc.terminate()
        decode_proc.join(timeout=2)
        if decode_proc.is_alive():
            decode_proc.kill()
        
        # Cleanup
        if os.path.exists(test_file):
            os.remove(test_file)

if __name__ == "__main__":
    print("Testing audio looping fix...\n")
    test_decode_looping()
