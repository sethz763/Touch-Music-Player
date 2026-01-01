"""
Commands Module - Re-export from canonical location

This module re-exports all commands from engine.commands to provide a
convenience import path. The canonical definitions are in engine/commands.py.

This exists for backward compatibility and convenience. All imports should
eventually consolidate to:
    from engine.commands import ...

Instead of mixing:
    from engine.commands import ...
    from engine.messages.commands import ...
"""

from engine.commands import (
    # Transport commands
    TransportPlay,
    TransportStop,
    TransportPause,
    TransportNext,
    TransportPrev,
    
    # Cue playback commands
    PlayCueCommand,
    StopCueCommand,
    FadeCueCommand,
    
    # Gain & fade commands
    SetMasterGainCommand,
    UpdateCueCommand,
    SetAutoFadeCommand,
    
    # Configuration commands
    OutputSetDevice,
    OutputSetConfig,
    OutputListDevices,
    
    # Output process commands
    OutputFadeTo,
)

__all__ = [
    # Transport commands
    "TransportPlay",
    "TransportStop",
    "TransportPause",
    "TransportNext",
    "TransportPrev",
    
    # Cue playback commands
    "PlayCueCommand",
    "StopCueCommand",
    "FadeCueCommand",
    
    # Gain & fade commands
    "SetMasterGainCommand",
    "UpdateCueCommand",
    "SetAutoFadeCommand",
    
    # Configuration commands
    "OutputSetDevice",
    "OutputSetConfig",
    "OutputListDevices",
    
    # Output process commands
    "OutputFadeTo",
]
