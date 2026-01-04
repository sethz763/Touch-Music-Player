import uuid
from datetime import datetime

from engine.audio_engine import AudioEngine
from engine.cue import CueInfo
from engine.messages.events import CueFinishedEvent


def test_cue_finished_emitted_even_if_cue_missing() -> None:
    """Regression: GUI must always get CueFinishedEvent to stop flashing.

    This covers cases like DecodeError where AudioEngine may have already removed
    the Cue from active_cues before the output process emits ("finished", cue_id, reason).
    """

    engine = AudioEngine(auto_fade_on_new=False)

    cue_id = uuid.uuid4().hex
    engine.cue_info_map[cue_id] = CueInfo(
        cue_id=cue_id,
        track_id="track-1",
        file_path="C:/nonexistent.wav",
        duration_seconds=None,
        started_at=datetime.now(),
    )

    # Simulate an earlier engine-side removal reason (e.g., DecodeError path)
    engine._removal_reasons[cue_id] = "decode_error"

    # Simulate the output process reporting completion after stop/error
    engine._out_evt_q.put(("finished", cue_id, "decode_error"))

    events = engine.pump()
    finished = [e for e in events if isinstance(e, CueFinishedEvent)]

    assert finished, "Expected CueFinishedEvent but none was emitted"
    assert finished[0].cue_info.cue_id == cue_id
    assert finished[0].reason == "decode_error"
