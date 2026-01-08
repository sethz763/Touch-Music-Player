#!/usr/bin/env python3
"""Regression test: GUI time normalization must work under global loop override.

Scenario:
- A cue is NOT marked loop_enabled per-cue.
- User enables loop override + global loop.
- Engine forces the active cue to loop; elapsed telemetry remains monotonic.
- Adapter must normalize elapsed/remaining for display so counters reset every loop.
"""

from __future__ import annotations

from queue import Queue

from gui.engine_adapter import EngineAdapter


def test_loop_override_enables_display_normalization_for_non_looping_cue():
    cmd_q: Queue = Queue()
    evt_q: Queue = Queue()
    adapter = EngineAdapter(cmd_q, evt_q)

    cue_id = "cue-override"

    # 2.0s trimmed loop window.
    adapter._cue_in_frames[cue_id] = 0
    adapter._cue_out_frames[cue_id] = 48000 * 2
    adapter._cue_sample_rates[cue_id] = 48000

    # Per-cue loop flag is off.
    adapter._cue_loop_enabled[cue_id] = False

    # Turn on override-driven looping.
    adapter.set_loop_override(True)
    adapter.set_global_loop_enabled(True)

    elapsed = 2.3
    total_file_seconds = 10.0

    display_elapsed = adapter._normalize_elapsed_for_display(cue_id, elapsed, total_file_seconds)
    remaining, display_total = adapter._calculate_trimmed_time(cue_id, elapsed, total_file_seconds)

    assert abs(display_total - 2.0) < 1e-6
    assert abs(display_elapsed - 0.3) < 1e-6
    assert abs(remaining - 1.7) < 1e-6


def test_override_without_global_loop_does_not_normalize():
    cmd_q: Queue = Queue()
    evt_q: Queue = Queue()
    adapter = EngineAdapter(cmd_q, evt_q)

    cue_id = "cue-override-off"

    adapter._cue_in_frames[cue_id] = 0
    adapter._cue_out_frames[cue_id] = 48000 * 2
    adapter._cue_sample_rates[cue_id] = 48000
    adapter._cue_loop_enabled[cue_id] = False

    adapter.set_loop_override(True)
    adapter.set_global_loop_enabled(False)

    elapsed = 2.3
    total_file_seconds = 10.0

    display_elapsed = adapter._normalize_elapsed_for_display(cue_id, elapsed, total_file_seconds)
    remaining, display_total = adapter._calculate_trimmed_time(cue_id, elapsed, total_file_seconds)

    # No looping for display; behaves like a normal trimmed cue past the end.
    assert abs(display_total - 2.0) < 1e-6
    assert abs(display_elapsed - 2.3) < 1e-6
    assert remaining == 0.0
