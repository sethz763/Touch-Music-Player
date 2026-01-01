"""
AudioEngine Public Event API

This module defines the complete set of events that the audio engine may emit to the GUI.
Events are immutable and are delivered via multiprocessing queues from the AudioService.

Version: 1.0 (FROZEN)
Stability: Stable - breaking changes require major version bump

Design Principles:
- All events are frozen dataclasses (immutable)
- Events are categorized as Lifecycle (guaranteed delivery), Telemetry (best-effort), or Diagnostics
- No Qt imports or GUI dependencies
- No side effects or logic in event classes
- Events are delivered from the AudioService event queue
- Delivery order within categories is NOT guaranteed (use timestamps if needed)

Event Categories:
1. LIFECYCLE EVENTS: Must be delivered exactly once, in order (via blocking put).
   - CueStartedEvent: Cue playback has begun
   - CueFinishedEvent: Cue playback completed

2. TELEMETRY EVENTS: Best-effort delivery (may be dropped if queue is full).
   - CueLevelsEvent: Per-cue RMS and peak levels (high frequency)
   - CueTimeEvent: Elapsed/remaining time for a cue (high frequency)
   - MasterLevelsEvent: Master output RMS and peak levels (high frequency)

3. DIAGNOSTIC EVENTS: Status information (best-effort).
   - DecodeErrorEvent: Decode process encountered an error
   - TransportStateEvent: Transport state changed
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from engine.cue import CueInfo

API_VERSION = "1.0"

# ==============================================================================
# LIFECYCLE EVENTS (Reliable, guaranteed delivery)
# ==============================================================================

@dataclass(frozen=True, slots=True)
class CueStartedEvent:
    """
    Emitted when a cue begins playback (audio output has started).
    
    Invariant: Exactly one CueStartedEvent is emitted per PlayCueCommand.
    Invariant: CueStartedEvent is always followed (eventually) by CueFinishedEvent.
    Invariant: This event is delivered via blocking put (guaranteed).
    Invariant: cue_id in this event matches the internal cue_id generated for that PlayCueCommand.
    
    Fields:
        cue_id: Unique identifier for this playback session.
        track_id: Application-provided track identifier (may be None).
        tod_start_iso: ISO 8601 timestamp when playback started (for logging/synchronization).
        file_path: Absolute path to the audio file being played.
    """
    cue_id: str
    track_id: str
    tod_start_iso: str
    file_path: str


@dataclass(frozen=True, slots=True)
class CueFinishedEvent:
    """
    Emitted when a cue stops playing (for any reason: EOF, stop command, error, etc.).
    
    Invariant: Exactly one CueFinishedEvent is emitted per CueStartedEvent.
    Invariant: CueFinishedEvent is the final event for a cue_id.
    Invariant: This event is delivered via blocking put (guaranteed).
    Invariant: cue_info contains immutable snapshot of CueInfo captured at playback start
              plus stopped_at timestamp set at playback end, and removal_reason populated.
    
    Fields:
        cue_info: Immutable CueInfo snapshot with complete metadata and timing.
                  Contains: cue_id, track_id, file_path, duration, gain, metadata, etc.
                  Also contains removal_reason describing why the cue was removed.
                  Use object type to avoid circular imports.
        reason: String indicating why playback finished (legacy, see cue_info.removal_reason):
               - "eof": Reached end of file naturally
               - "stopped": Received StopCueCommand
               - "error": Decode or playback error occurred
               - "forced": Force-removed due to stuck cue or other anomaly
               - "manual_fade": Manually faded out from GUI
               - "auto_fade": Auto-faded when starting new track
    """
    cue_info: object  # CueInfo (frozen snapshot with removal_reason)
    reason: str


# ==============================================================================
# TELEMETRY EVENTS (Best-effort delivery, may be dropped if queue is full)
# ==============================================================================

@dataclass(frozen=True, slots=True)
class CueLevelsEvent:
    """
    Per-cue audio level snapshot (RMS and peak, per-channel).
    
    Invariant: Emitted at high frequency (~20-50Hz) while cue is playing.
    Invariant: May be dropped if telemetry queue is full (best-effort).
    Invariant: Use RMS for visual meters, peak for clipping detection.
    Invariant: Values are in linear amplitude (0.0 = silence, 1.0 = unity).
    
    Fields:
        cue_id: Identifier of the cue being metered.
        rms: RMS (root mean square) level(s). Can be:
             - Single float for mono/legacy (0.0 to 1.0+, clipping if > 1.0)
             - List of floats for per-channel (one per output channel)
        peak: Peak absolute amplitude. Can be:
              - Single float for mono/legacy (0.0 to 1.0+)
              - List of floats for per-channel (one per output channel)
        rms_per_channel: Optional list of per-channel RMS values (new format).
                        If provided, overrides rms for per-channel meters.
        peak_per_channel: Optional list of per-channel peak values (new format).
                         If provided, overrides peak for per-channel meters.
    """
    cue_id: str
    rms: float | list
    peak: float | list
    rms_per_channel: Optional[list] = None
    peak_per_channel: Optional[list] = None


@dataclass(frozen=True, slots=True)
class CueTimeEvent:
    """
    Time reporting for a playing cue (elapsed and remaining time).
    
    Invariant: Emitted at high frequency (~20-50Hz) while cue is playing.
    Invariant: May be dropped if telemetry queue is full (best-effort).
    Invariant: elapsed_seconds is monotonically increasing within a cue.
    Invariant: remaining_seconds = total_seconds - elapsed_seconds (when known).
    Invariant: If total_seconds is None, remaining_seconds may be 0.0 or estimated.
    
    Fields:
        cue_id: Identifier of the cue being timed.
        elapsed_seconds: Playback time since CueStartedEvent, in seconds.
        remaining_seconds: Estimated time until cue finishes, in seconds.
        total_seconds: Total cue duration if known (from file probe or PlayCueCommand.total_seconds).
                      May be None if duration unknown.
    """
    cue_id: str
    elapsed_seconds: float
    remaining_seconds: float
    total_seconds: Optional[float] = None


@dataclass(frozen=True, slots=True)
class MasterLevelsEvent:
    """
    Master output audio level snapshot (per-channel RMS and peak in dB).
    
    Invariant: Emitted at high frequency (~20-50Hz) if any cue is playing.
    Invariant: May be dropped if telemetry queue is full (best-effort).
    Invariant: Represents the mix of all active cues on the output.
    
    Fields:
        rms: List of per-channel RMS levels in dB (e.g., [-6.5, -7.2] for stereo).
        peak: List of per-channel peak levels in dB (e.g., [-3.0, -4.1] for stereo).
    """
    rms: list
    peak: list


# ==============================================================================
# BATCHED TELEMETRY EVENTS (High-frequency, aggregated per update cycle)
# ==============================================================================

@dataclass(frozen=True, slots=True)
class BatchCueLevelsEvent:
    """
    Aggregated audio level updates for multiple cues in a single message.
    
    Replaces individual CueLevelsEvent messages to reduce queue overhead.
    Invariant: Emitted once per output callback cycle when cues are active.
    Invariant: Contains all cues with updated levels for this cycle.
    
    Fields:
        cue_levels: Dict mapping cue_id -> (rms, peak) tuple for mixed levels.
                    Example: {"cue1": (0.5, 0.7), "cue2": (0.3, 0.4)}
        cue_levels_per_channel: Dict mapping cue_id -> (rms_list, peak_list) for per-channel levels.
                                Example: {"cue1": ([0.5, 0.6], [0.7, 0.8]), ...}
                                If present, UI should prefer this over cue_levels.
    """
    cue_levels: dict  # {cue_id: (rms, peak), ...}
    cue_levels_per_channel: Optional[dict] = None  # {cue_id: (rms_list, peak_list), ...}


@dataclass(frozen=True, slots=True)
class BatchCueTimeEvent:
    """
    Aggregated time updates for multiple cues in a single message.
    
    Replaces individual CueTimeEvent messages to reduce queue overhead.
    Invariant: Emitted once per output callback cycle when cues are active.
    Invariant: Contains all cues with updated time for this cycle.
    
    Fields:
        cue_times: Dict mapping cue_id -> (elapsed_seconds, remaining_seconds) tuple.
                   Example: {"cue1": (2.5, 7.5), "cue2": (1.2, 8.8)}
    """
    cue_times: dict  # {cue_id: (elapsed_seconds, remaining_seconds), ...}


# ==============================================================================
# DIAGNOSTIC EVENTS (Status and error reporting)
# ==============================================================================

@dataclass(frozen=True, slots=True)
class DecodeErrorEvent:
    """
    Emitted when the decode process encounters an error.
    
    Invariant: Implies that the associated cue will be stopped.
    Invariant: May be followed by CueFinishedEvent with reason="error".
    Invariant: Error details are in the error field (string message).
    
    Fields:
        cue_id: Identifier of the cue that failed to decode.
        track_id: Application-provided track identifier.
        file_path: Path to the file that failed to decode.
        error: Human-readable error message.
    """
    cue_id: str
    track_id: str
    file_path: str
    error: str


@dataclass(frozen=True, slots=True)
class TransportStateEvent:
    """
    Emitted when the transport state changes (future use - currently unimplemented).
    
    Fields:
        state: Transport state string: "playing", "paused", or "stopped".
    """
    state: str


# ==============================================================================
# LEGACY / COMPATIBILITY EVENTS
# ==============================================================================
# These events may be emitted as tuples from internal processes.
# They are not part of the public API but are documented for reference.
#
# ("finished", cue_id): Internal signal that a cue finished (converted to CueFinishedEvent).
# ("started", cue_id, track_id, file_path, total_seconds): Decoder ready (internal).
# ("status", status_str): Audio stream status (converted to diagnostic).
# ("device_changed", device_id): Output device changed.
# ("config_changed", {sample_rate, channels, block_frames}): Output config changed.
# ("devices", device_list): List of available devices (response to OutputListDevices).
# ("cue_levels", cue_id, rms, peak): Converted to CueLevelsEvent.
# ("cue_time", cue_id, elapsed, remaining): Converted to CueTimeEvent.
# ("debug", msg): Debug log message (internal).
