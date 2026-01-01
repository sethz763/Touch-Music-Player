from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, Optional
from PySide6.QtCore import QObject, Signal
from log.log_record import LogRecord, CueLogRecord

class LogManager(QObject):
    # Signal emitted when a cue finishes and is logged
    cue_finished_logged = Signal(CueLogRecord)
    
    def __init__(self):
        super().__init__()
    
    def info(self, *, cue_id: str = "", track_id: str = "", tod_start: Optional[datetime] = None, source: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        if metadata is None:
            metadata = {}
        if tod_start is None:
            tod_start = datetime.now()
        rec = LogRecord(cue_id=cue_id or "", track_id=track_id or "", tod_start=tod_start, source=source, message=message, metadata=metadata)
        ts = rec.tod_start.isoformat(timespec="milliseconds")
        print(f"[{ts}] [{rec.source}] cue={rec.cue_id} track={rec.track_id} {rec.message} {rec.metadata}")
    
    def log_cue_finished(self, cue_log_record: CueLogRecord) -> None:
        """Log a finished cue and emit signal for listeners (UI, export, etc.)."""
        try:
            # Log to console
            ts = cue_log_record.started_at.isoformat(timespec="milliseconds")
            print(f"[{ts}] [cue_finished] cue={cue_log_record.cue_id} track={cue_log_record.track_id} file={cue_log_record.file_path} reason={cue_log_record.reason}")
            
            # Emit signal so all listeners (Save_To_Excel, UI, export, etc.) can handle it
            self.cue_finished_logged.emit(cue_log_record)
        except Exception as e:
            print(f"Error logging cue finished: {e}")
