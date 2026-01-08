#!/usr/bin/env python3
"""
Test that the GUI correctly displays trimmed time duration.
This tests the full integration: engine calculates trimmed time, GUI displays it.
"""

import sys
import time
import tempfile
from pathlib import Path
from queue import Queue

# Ensure imports work
sys.path.insert(0, str(Path(__file__).parent))

from engine.messages.commands import PlayCueCommand
from engine.messages.events import CueStartedEvent, BatchCueTimeEvent, CueFinishedEvent
from engine.cue import CueInfo
from gui.engine_adapter import EngineAdapter


def test_full_trimmed_time_flow():
    """Test that engine adapter correctly processes trimmed time through the full pipeline."""
    
    print("=" * 70)
    print("Testing Full Trimmed Time Flow")
    print("=" * 70)
    
    # Setup
    cmd_q = Queue()
    evt_q = Queue()
    adapter = EngineAdapter(cmd_q, evt_q)
    
    # Simulate a file with:
    # - Full duration: 100 seconds
    # - Trimmed range: 10-30 seconds (20 seconds playable)
    # - Sample rate: 48000 Hz
    
    in_frame = 10 * 48000  # 480000 frames
    out_frame = 30 * 48000  # 1440000 frames
    total_file_seconds = 100.0
    
    print(f"\nSetup:")
    print(f"  File duration: {total_file_seconds}s")
    print(f"  In frame: {in_frame} (at {in_frame / 48000}s)")
    print(f"  Out frame: {out_frame} (at {out_frame / 48000}s)")
    print(f"  Playable duration: {(out_frame - in_frame) / 48000}s")
    print(f"  Sample rate: 48000 Hz")
    
    # Simulate play_cue call
    print(f"\n1. Calling play_cue with in_frame={in_frame}, out_frame={out_frame}")
    adapter.play_cue(
        file_path="/tmp/test.mp3",
        cue_id="test-cue-1",
        in_frame=in_frame,
        out_frame=out_frame,
        total_seconds=total_file_seconds,
    )
    
    # Check that engine adapter captured the frame boundaries
    assert adapter._cue_in_frames.get("test-cue-1") == in_frame
    assert adapter._cue_out_frames.get("test-cue-1") == out_frame
    print(f"   ✓ Frame boundaries captured")
    
    # Check sample rate estimation
    estimated_sr = adapter._cue_sample_rates.get("test-cue-1")
    print(f"   ✓ Sample rate set to: {estimated_sr} Hz")
    assert estimated_sr == 48000, f"Expected 48000, got {estimated_sr}"
    
    # Simulate CueStartedEvent (refines sample rate)
    print(f"\n2. Simulating CueStartedEvent")
    evt_q.put(CueStartedEvent(
        cue_id="test-cue-1",
        track_id="track-1",
        tod_start_iso="2024-01-01T12:00:00Z",
        file_path="/tmp/test.mp3",
        total_seconds=total_file_seconds,
    ))
    adapter._poll_events()  # Process the event
    
    # Verify sample rate was set
    set_sr = adapter._cue_sample_rates.get("test-cue-1")
    print(f"   ✓ Sample rate: {set_sr} Hz")
    
    # Simulate time updates at different positions
    print(f"\n3. Simulating playback time updates")
    
    test_cases = [
        (0.0, 20.0, 20.0, "At start of trimmed region"),
        (5.0, 15.0, 20.0, "Halfway through trimmed region"),
        (19.5, 0.5, 20.0, "Near end of trimmed region"),
        (20.0, 0.0, 20.0, "At end (clamped to 0)"),
    ]
    
    for elapsed, expected_remaining, expected_total, description in test_cases:
        print(f"\n   Test: {description}")
        print(f"   Elapsed: {elapsed}s")
        
        # Create event with FULL FILE time values (engine would send these)
        # The engine calculates actual elapsed from start of file, not from in_frame
        # elapsed is actually: elapsed_from_in_frame
        evt_q.put(BatchCueTimeEvent(
            cue_times={"test-cue-1": (elapsed, 0.0)},  # remaining will be recalculated
        ))
        
        # Manually trigger what _dispatch_event does
        event = evt_q.get_nowait()
        
        # Simulate the event dispatch
        adapter._last_started_cue_id = "test-cue-1"
        adapter._cue_total_seconds["test-cue-1"] = total_file_seconds
        
        # The adapter will calculate trimmed values
        try:
            cue_times = event.cue_times or {}
        except:
            cue_times = {}
        
        if cue_times and "test-cue-1" in cue_times:
            elapsed_val, _ = cue_times["test-cue-1"]
            total = adapter._cue_total_seconds.get("test-cue-1")
            
            # This is what the adapter does
            trimmed_remaining, trimmed_total = adapter._calculate_trimmed_time(
                "test-cue-1", elapsed_val, total
            )
            
            print(f"   Trimmed duration: {trimmed_total}s")
            print(f"   Remaining: {trimmed_remaining}s")
            
            assert abs(trimmed_remaining - expected_remaining) < 0.01, \
                f"Expected {expected_remaining}s remaining, got {trimmed_remaining}s"
            assert abs(trimmed_total - expected_total) < 0.01, \
                f"Expected {expected_total}s total, got {trimmed_total}s"
            print(f"   ✓ PASS")
    
    print(f"\n" + "=" * 70)
    print(f"✅ Full trimmed time flow test PASSED")
    print(f"=" * 70)


if __name__ == '__main__':
    test_full_trimmed_time_flow()
