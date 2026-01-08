#!/usr/bin/env python3
"""Test that engine adapter correctly calculates trimmed duration."""

import sys
import time
from pathlib import Path

# Ensure we can import our code
sys.path.insert(0, str(Path(__file__).parent))

from gui.engine_adapter import EngineAdapter
from engine.messages.commands import PlayCueCommand
from engine.messages.events import CueStartedEvent, CueTimeEvent, BatchCueTimeEvent


def test_trimmed_time_calculation():
    """Test the _calculate_trimmed_time helper method."""
    
    # Create adapter (no queues needed for this test)
    from queue import Queue
    cmd_q = Queue()
    evt_q = Queue()
    
    adapter = EngineAdapter(cmd_q, evt_q)
    
    print("=== Test 1: Full file (no in/out frame) ===")
    adapter._cue_in_frames['cue1'] = 0
    adapter._cue_out_frames['cue1'] = None  # No out frame
    adapter._cue_sample_rates['cue1'] = 48000
    
    remaining, total = adapter._calculate_trimmed_time('cue1', 2.0, 10.0)
    print(f"Elapsed: 2.0s, Total: 10.0s")
    print(f"Result: remaining={remaining:.2f}s, total={total:.2f}s")
    assert abs(remaining - 8.0) < 0.01, f"Expected 8.0s remaining, got {remaining}"
    assert abs(total - 10.0) < 0.01, f"Expected 10.0s total, got {total}"
    print("✓ PASS\n")
    
    print("=== Test 2: Trimmed file (48000 samples = 1.0s at 48kHz) ===")
    adapter._cue_in_frames['cue2'] = 0
    adapter._cue_out_frames['cue2'] = 48000  # 1 second at 48kHz
    adapter._cue_sample_rates['cue2'] = 48000
    
    remaining, total = adapter._calculate_trimmed_time('cue2', 0.5, 10.0)
    print(f"In: 0, Out: 48000 frames at 48kHz = 1.0s")
    print(f"Elapsed: 0.5s")
    print(f"Result: remaining={remaining:.2f}s, total={total:.2f}s")
    assert abs(remaining - 0.5) < 0.01, f"Expected 0.5s remaining, got {remaining}"
    assert abs(total - 1.0) < 0.01, f"Expected 1.0s total, got {total}"
    print("✓ PASS\n")
    
    print("=== Test 3: Trimmed from middle (in=24000, out=72000 = 1.0s at 48kHz) ===")
    adapter._cue_in_frames['cue3'] = 24000
    adapter._cue_out_frames['cue3'] = 72000  # 1 second at 48kHz
    adapter._cue_sample_rates['cue3'] = 48000
    
    remaining, total = adapter._calculate_trimmed_time('cue3', 0.3, 10.0)
    print(f"In: 24000, Out: 72000 frames at 48kHz = 1.0s")
    print(f"Elapsed: 0.3s")
    print(f"Result: remaining={remaining:.2f}s, total={total:.2f}s")
    assert abs(remaining - 0.7) < 0.01, f"Expected 0.7s remaining, got {remaining}"
    assert abs(total - 1.0) < 0.01, f"Expected 1.0s total, got {total}"
    print("✓ PASS\n")
    
    print("=== Test 4: Past end of cue ===")
    adapter._cue_in_frames['cue4'] = 0
    adapter._cue_out_frames['cue4'] = 48000
    adapter._cue_sample_rates['cue4'] = 48000
    
    remaining, total = adapter._calculate_trimmed_time('cue4', 1.5, 10.0)
    print(f"Elapsed: 1.5s (past 1.0s cue end)")
    print(f"Result: remaining={remaining:.2f}s, total={total:.2f}s")
    assert remaining == 0.0, f"Expected 0.0s remaining when past end, got {remaining}"
    assert abs(total - 1.0) < 0.01, f"Expected 1.0s total, got {total}"
    print("✓ PASS\n")
    
    print("=== Test 5: No sample rate (fallback) ===")
    adapter._cue_in_frames['cue5'] = 0
    adapter._cue_out_frames['cue5'] = 48000
    # Don't set sample rate (just skip setting it)
    # Note: cue5 won't be in _cue_sample_rates
    
    remaining, total = adapter._calculate_trimmed_time('cue5', 2.0, 10.0)
    print(f"No sample rate (should fallback)")
    print(f"Result: remaining={remaining:.2f}s, total={total:.2f}s")
    assert abs(remaining - 8.0) < 0.01, f"Expected 8.0s remaining (fallback), got {remaining}"
    assert abs(total - 10.0) < 0.01, f"Expected 10.0s total (fallback), got {total}"
    print("✓ PASS\n")
    
    print("=" * 50)
    print("✅ ALL TESTS PASSED")
    print("=" * 50)


if __name__ == '__main__':
    test_trimmed_time_calculation()
