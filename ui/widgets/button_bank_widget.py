from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional
from PySide6.QtWidgets import QWidget, QGridLayout
from PySide6.QtCore import Signal

if TYPE_CHECKING:
    from ui.widgets.sound_file_button import SoundFileButton
    from gui.engine_adapter import EngineAdapter


class ButtonBankWidget(QWidget):
    """
    Grid of audio clip buttons with intelligent event routing.
    
    Each button manages a single audio cue and emits playback requests to the engine adapter.
    This widget routes engine events to the correct button based on cue_id to avoid 
    broadcasting events to all 12 buttons (which causes performance issues).
    """

    def __init__(self, rows: int = 3, cols: int = 8, engine_adapter: EngineAdapter | None = None) -> None:
        """
        Initialize button bank with grid of sound file buttons.
        
        Args:
            rows (int): Number of rows in button grid
            cols (int): Number of columns in button grid
            engine_adapter (EngineAdapter or None): Reference to engine adapter for connecting button signals
        """
        super().__init__()
        self.engine_adapter = engine_adapter
        self.buttons = []
        self.setFixedHeight(500)
        
        # Import here to avoid module-level import in subprocess (avoids pickling issues)
        from ui.widgets.sound_file_button import SoundFileButton
        
        layout = QGridLayout(self)
        layout.setSpacing(6)
        
        for r in range(rows):
            for c in range(cols):
                btn: SoundFileButton = SoundFileButton()
                # Connect button's playback request signals to ButtonBankWidget for routing
                btn.request_play.connect(self._on_button_request_play)
                btn.request_stop.connect(self._on_button_request_stop)
                btn.request_fade.connect(self._on_button_request_fade)
                self.buttons.append(btn)
                layout.addWidget(btn, r, c)
        
        # Connect engine adapter signals to button bank's routing methods
        if engine_adapter:
            self.set_engine_adapter(engine_adapter)
    
    def _on_button_request_play(self, file_path: str, params: dict) -> None:
        """
        Handle play request from a button.
        Unpacks params dict and forwards to engine adapter with all playback parameters.
        
        Args:
            file_path (str): Path to audio file
            params (dict): Playback parameters including cue_id
        """
        if self.engine_adapter:
            self.engine_adapter.play_cue(file_path=file_path, **params)
    
    def _on_button_request_stop(self, cue_id: str, fade_out_ms: int) -> None:
        """
        Handle stop request from a button.
        
        Args:
            cue_id (str): Identifier of cue to stop
            fade_out_ms (int): Fade-out duration
        """
        if self.engine_adapter:
            self.engine_adapter.stop_cue(cue_id, fade_out_ms)
    
    def _on_button_request_fade(self, cue_id: str, target_db: float, duration_ms: int) -> None:
        """
        Handle fade request from a button.
        
        Args:
            cue_id (str): Identifier of cue to fade
            target_db (float): Target gain in dB
            duration_ms (int): Fade duration
        """
        if self.engine_adapter:
            self.engine_adapter.fade_cue(cue_id, target_db, duration_ms)
    
    def set_engine_adapter(self, engine_adapter: EngineAdapter) -> None:
        """
        Set or update engine adapter reference and subscribe all buttons to signals.
        
        Args:
            engine_adapter (EngineAdapter): The engine adapter instance
        """
        self.engine_adapter = engine_adapter
        
        # Subscribe each button directly to engine adapter signals
        # Each button filters events based on its own _active_cue_ids set
        for btn in self.buttons:
            btn.subscribe_to_adapter(engine_adapter)