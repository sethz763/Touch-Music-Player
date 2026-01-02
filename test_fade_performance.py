#!/usr/bin/env python3
"""
Test script to reproduce GUI lock-up during simultaneous fade-outs with new cue playback.

This script:
1. Starts multiple cues playing simultaneously
2. Triggers fades on all cues
3. Starts a new cue while fades are happening
4. Captures timing data from instrumentation in engine_adapter.py

Run this to see [PERF] timing messages in console output.

Usage:
    python test_fade_performance.py
"""

import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QLabel
from PySide6.QtCore import Qt, QTimer

from gui.engine_adapter import EngineAdapter
from engine.cue import CueInfo
import multiprocessing as mp


class FadePerformanceTest(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Create queues for engine communication
        self.cmd_q = mp.Queue()
        self.evt_q = mp.Queue()
        
        # Create adapter
        self.adapter = EngineAdapter(self.cmd_q, self.evt_q, parent=self)
        
        # Setup UI
        self.setWindowTitle("Fade Performance Test")
        self.setGeometry(100, 100, 300, 400)
        
        widget = QWidget()
        self.setCentralWidget(widget)
        layout = QVBoxLayout(widget)
        
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)
        
        # Test button
        btn_test = QPushButton("Run: Start 5 Cues")
        btn_test.clicked.connect(self.run_test)
        layout.addWidget(btn_test)
        
        btn_fade = QPushButton("Fade All Out (while playing)")
        btn_fade.clicked.connect(self.fade_all_out)
        layout.addWidget(btn_fade)
        
        btn_new = QPushButton("Start New Cue During Fade")
        btn_new.clicked.connect(self.start_new_cue_during_fade)
        layout.addWidget(btn_new)
        
        self.cue_ids = []
        self.log_text = ""
        
    def log(self, msg):
        """Log message with timestamp"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text += f"[{timestamp}] {msg}\n"
        self.status_label.setText(msg)
        print(msg)
    
    def run_test(self):
        """Start 5 dummy cues simultaneously"""
        self.log("Starting 5 cues...")
        
        # For this test, we just send PlayCueCommand to the adapter
        # In real scenario, these would be actual audio files
        # But for testing the adapter instrumentation, we'll just send commands
        
        for i in range(5):
            cue_id = f"test_cue_{i}"
            self.cue_ids.append(cue_id)
            # Simulate cue playback request
            self.adapter.play_cue(
                file_path=f"/fake/path/cue_{i}.wav",
                cue_id=cue_id,
                track_id=f"track_{i}",
                gain_db=-6.0 if i > 0 else 0.0,  # First cue at unity, others at -6dB
            )
            self.log(f"  Sent play_cue: {cue_id}")
        
        self.log(f"Sent {len(self.cue_ids)} play commands")
        
        # Schedule the fade test
        QTimer.singleShot(1000, self.fade_all_out)
    
    def fade_all_out(self):
        """Fade all cues out simultaneously"""
        self.log(f"Fading out {len(self.cue_ids)} cues simultaneously...")
        
        # Send fade commands for all cues
        # This should trigger the [PERF] timing output if it takes > 5ms
        fade_start = time.perf_counter()
        
        for i, cue_id in enumerate(self.cue_ids):
            # Stagger fade start times slightly to simulate realistic scenario
            self.adapter.fade_cue(
                cue_id=cue_id,
                target_db=-60.0,  # Fade to silent
                duration_ms=2000,  # 2 second fade
                curve="equal_power",
            )
            self.log(f"  Sent fade command: {cue_id}")
        
        fade_time = (time.perf_counter() - fade_start) * 1000
        self.log(f"Sent {len(self.cue_ids)} fade commands in {fade_time:.2f}ms total")
        
        # Schedule new cue start during fade
        QTimer.singleShot(500, self.start_new_cue_during_fade)
    
    def start_new_cue_during_fade(self):
        """Start a new cue while others are fading"""
        self.log("Starting NEW cue while 5 cues are fading out...")
        
        new_cue_id = "new_cue"
        start = time.perf_counter()
        
        self.adapter.play_cue(
            file_path="/fake/path/new_cue.wav",
            cue_id=new_cue_id,
            track_id="new_track",
            gain_db=0.0,
        )
        
        elapsed = (time.perf_counter() - start) * 1000
        self.log(f"New cue request took {elapsed:.2f}ms")
        self.log("Test complete. Check console for [PERF] timing data.")


def main():
    app = QApplication(sys.argv)
    window = FadePerformanceTest()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
