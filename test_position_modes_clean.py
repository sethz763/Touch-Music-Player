#!/usr/bin/env python3
"""
Test script to verify the two position calculation modes.

This tests the set_engine_position_relative_to_trim_markers() configuration
by creating an adapter and verifying both modes produce correct outputs.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import multiprocessing as mp
from gui.engine_adapter import EngineAdapter


def test_position_modes():
    """Test both trimmed and absolute position calculation modes."""
    
    # Create dummy queues
    cmd_q = mp.Queue()
    evt_q = mp.Queue()
    
    # Create adapter
    adapter = EngineAdapter(cmd_q, evt_q, parent=None)
    
    # Simulate a cue with trim boundaries
    cue_id = "test-cue"
    in_frame = 24000    # 0.5s at 48kHz
    out_frame = 72000   # 1.5s at 48kHz
    sr = 48000
    total_duration = 2.0
    
    # Register the cue boundaries
    adapter._cue_in_frames[cue_id] = in_frame
    adapter._cue_out_frames[cue_id] = out_frame
    adapter._cue_sample_rates[cue_id] = sr
    adapter._cue_total_seconds[cue_id] = total_duration
    
    print("=" * 70)
    print("Testing Position Mode Configuration")
    print("=" * 70)
    print(f"\nCue setup:")
    print(f"  in_frame: {in_frame} (0.5s)")
    print(f"  out_frame: {out_frame} (1.5s)")
    print(f"  sample_rate: {sr}")
    print(f"  trimmed_duration: {(out_frame - in_frame) / sr}s")
    
    # Test Mode 1: Trimmed time (default)
    print(f"\n" + "=" * 70)
    print("MODE 1: Trimmed Time (relative to in_frame/out_frame)")
    print("=" * 70)
    adapter.set_engine_position_relative_to_trim_markers(True)
    
    test_cases = [
        (0.0, "Start"),
        (0.5, "Mid-playback"),
        (1.0, "Near end"),
    ]
    
    for elapsed, desc in test_cases:
        remaining, total = adapter._calculate_trimmed_time(cue_id, elapsed, total_duration)
        expected_remaining = 1.0 - elapsed
        print(f"\n  {desc} (elapsed={elapsed}s):")
        print(f"    remaining: {remaining:.4f}s (expected: {expected_remaining:.4f}s)")
        print(f"    total: {total:.4f}s (expected: 1.0000s)")
        assert abs(remaining - expected_remaining) < 0.0001, f"Remaining mismatch: {remaining} vs {expected_remaining}"
        assert abs(total - 1.0) < 0.0001, f"Total mismatch: {total} vs 1.0"
        print(f"    [PASS]")
    
    # Test Mode 2: Absolute file position
    print(f"\n" + "=" * 70)
    print("MODE 2: Absolute File Position (no trim adjustment to elapsed)")
    print("=" * 70)
    adapter.set_engine_position_relative_to_trim_markers(False)
    
    test_cases_abs = [
        (0.5, "At in_frame"),
        (1.0, "Mid-playback"),
        (1.5, "At out_frame"),
    ]
    
    for elapsed, desc in test_cases_abs:
        remaining, total = adapter._calculate_trimmed_time(cue_id, elapsed, total_duration)
        expected_remaining = 1.5 - elapsed  # out_frame/sr - elapsed
        print(f"\n  {desc} (elapsed={elapsed}s):")
        print(f"    remaining: {remaining:.4f}s (expected: {expected_remaining:.4f}s)")
        print(f"    total: {total:.4f}s (expected: 1.0000s)")
        assert abs(remaining - expected_remaining) < 0.0001, f"Remaining mismatch: {remaining} vs {expected_remaining}"
        assert abs(total - 1.0) < 0.0001, f"Total mismatch: {total} vs 1.0"
        print(f"    [PASS]")
    
    print(f"\n" + "=" * 70)
    print("All tests PASSED")
    print("=" * 70)


if __name__ == "__main__":
    try:
        test_position_modes()
    except Exception as e:
        print(f"\n[TEST FAILED]: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
