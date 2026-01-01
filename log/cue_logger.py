from __future__ import annotations

from datetime import datetime
from typing import Optional

from engine.cue import CueInfo
from engine.messages.events import CueFinishedEvent
from log.Save_To_Excel import Save_To_Excel
from log.log_record import CueLogRecord
from log.log_manager import LogManager


class CueLogger:
    """
    Adapter between Engine events and logging system.

    Responsibilities:
    - Listen for CueFinishedEvent
    - Extract immutable CueInfo
    - Create CueLogRecord and emit via LogManager
    - Convert CueLogRecord to Excel format and save
    - LogManager forwards to all listeners (UI, export, etc.)

    This class contains NO UI logic and NO audio logic.
    """

    def __init__(self, log_manager: LogManager, save_to_excel: Optional[Save_To_Excel] = None):
        self._log_manager = log_manager
        self._save_to_excel = save_to_excel

    def on_cue_finished(self, evt: CueFinishedEvent) -> None:
        """
        Handle a finished cue and create a log record.

        This method extracts fields from the immutable CueInfo snapshot
        and creates a CueLogRecord for logging.
        """
        cue_info: CueInfo = evt.cue_info

        # Defensive: should never happen, but don't crash logging
        if cue_info is None:
            return

        # Construct CueLogRecord from CueInfo
        cue_log_record = CueLogRecord(
            cue_id=cue_info.cue_id,
            track_id=cue_info.track_id,
            file_path=cue_info.file_path,
            started_at=cue_info.started_at or datetime.now(),
            stopped_at=cue_info.stopped_at or datetime.now(),
            duration_seconds=cue_info.duration_seconds,
            in_frame=cue_info.in_frame,
            out_frame=cue_info.out_frame,
            gain_db=cue_info.gain_db,
            fade_in_ms=cue_info.fade_in_ms,
            fade_out_ms=cue_info.fade_out_ms,
            reason=evt.reason,
            metadata=cue_info.metadata or {},
        )

        # Emit via LogManager so all listeners handle it
        self._log_manager.log_cue_finished(cue_log_record)
        
        # Write to Excel if available
        if self._save_to_excel is not None:
            self._log_cue_to_excel(cue_log_record)

    def _log_cue_to_excel(self, cue_log_record: CueLogRecord) -> None:
        """
        Convert CueLogRecord to Excel format and write to spreadsheet.
        
        Args:
            cue_log_record: The log record to write
        """
        try:
            # Convert CueLogRecord to legacy Save_To_Excel format
            time_start = cue_log_record.started_at
            time_end = cue_log_record.stopped_at
            
            try:
                duration_played = time_end - time_start
            except Exception:
                duration_played = None
            
            # Extract metadata safely with flexible key matching and byte string handling
            metadata = cue_log_record.metadata or {}
            
            # Convert metadata values from bytes to strings if needed
            clean_metadata = {}
            for k, v in metadata.items():
                key_str = k.decode('utf-8') if isinstance(k, bytes) else str(k)
                val_str = v.decode('utf-8') if isinstance(v, bytes) else str(v)
                clean_metadata[key_str] = val_str
            
            # Try multiple common key names for artist (case-insensitive)
            artist = ""
            for key in ["Artist", "artist", "ARTIST", "TPE1", "ALBUM ARTIST", "album_artist"]:
                if key in clean_metadata:
                    artist = clean_metadata[key]
                    break
            
            # Try multiple common key names for title (case-insensitive)
            song = ""
            for key in ["Title", "title", "TITLE", "TIT2", "SONG", "song"]:
                if key in clean_metadata:
                    song = clean_metadata[key]
                    break
            
            log_data = {
                "ARTIST": artist,
                "SONG": song,
                "FILENAME": cue_log_record.file_path,
                "TIME_START": time_start.strftime("%H:%M:%S") if time_start else "",
                "TIME_END": time_end.strftime("%H:%M:%S") if time_end else "",
                "DURATION_PLAYED": str(duration_played) if duration_played else "",
                "CUE_ID": cue_log_record.cue_id,
                "TRACK_ID": cue_log_record.track_id,
                "IN_FRAME": cue_log_record.in_frame,
                "OUT_FRAME": cue_log_record.out_frame if cue_log_record.out_frame is not None else "",
                "GAIN_DB": cue_log_record.gain_db,
                "DURATION_SECONDS": cue_log_record.duration_seconds if cue_log_record.duration_seconds is not None else "",
                "FADE_IN_MS": cue_log_record.fade_in_ms,
                "FADE_OUT_MS": cue_log_record.fade_out_ms,
                "END_REASON": cue_log_record.reason,
            }
            
            # Write to Excel
            self._save_to_excel.update_log(log_data)
        except Exception as e:
            pass
