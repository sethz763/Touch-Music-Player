# AudioLevelMeter Quick Reference

## What Was Added

AudioLevelMeters are now displayed in the `SoundFileButton` widget when you swipe to reveal the gain slider. They show real-time audio levels for the left and right channels (if available).

## How to Use

### Basic Interaction
1. **Swipe Left** on a button → Gain slider appears with animated level meters
2. **Monitor Levels** → Watch meters while audio plays
3. **Swipe Right** → Slider and meters disappear with smooth animation

### Visual Indicators
- **Green bars** → Good level (-64 to -12 dB)
- **Yellow bars** → Normal level (-12 to -6 dB)
- **Orange bars** → Hot level (-6 to -1 dB)
- **Red bars** → Clipping zone (0 dB)
- **Red line** → Peak hold indicator

## For Developers

### Working with the Code

#### Checking Meter Visibility
```python
button = SoundFileButton("My Track")
if button.level_meter_left.isVisible():
    print("Meters are shown")
```

#### Manually Showing/Hiding Meters
```python
button._show_gain_slider()  # Shows slider and meters
button._hide_gain_slider()  # Hides slider and meters
```

#### Updating Meter Display
The meters update automatically via the `cue_levels` signal:
```python
# Called from engine adapter (automatic)
def _on_cue_levels(self, cue_id: str, rms: float, peak: float):
    # Meters update here
    self.level_meter_left.setValue(rms_db, peak_db)
```

#### Accessing Meter Objects
```python
# Get the meter widgets
left_meter = button.level_meter_left   # AudioLevelMeter instance
right_meter = button.level_meter_right # AudioLevelMeter instance

# Check properties
print(left_meter.value)  # Current level (dB)
print(left_meter.peak)   # Peak level (dB)
```

### Customizing the Meters

#### Change Meter Width
In `_setup_gain_slider()`, find:
```python
self.level_meter_left = AudioLevelMeter(vmin=-64, vmax=0, height=self.height(), width=10)
```
Change `width=10` to desired pixel width.

#### Adjust dB Range
```python
# Currently: -64 to 0 dB
# To change range:
self.level_meter_left = AudioLevelMeter(vmin=-100, vmax=6, ...)
```

#### Change Animation Speed
In animation methods, find:
```python
self.slider_animation.setDuration(300)  # milliseconds
```
Smaller = faster, larger = slower

#### Customize Colors
Edit the meter's `paintEvent` in [AudioLevelMeter.py](ui/widgets/AudioLevelMeter.py):
```python
colors = ['green', 'yellow', 'orange', 'red']  # Adjust these
```

## Architecture

### Key Components

1. **AudioLevelMeter Widget** ([ui/widgets/AudioLevelMeter.py](ui/widgets/AudioLevelMeter.py))
   - Vertical bar display
   - -64 to 0 dB range (configurable)
   - Color-coded levels
   - Peak hold indicator

2. **SoundFileButton Integration** ([ui/widgets/sound_file_button.py](ui/widgets/sound_file_button.py))
   - Creates meter instances
   - Positions meters with slider
   - Animates meters with slider
   - Updates meters from cue_levels events

3. **Engine Adapter** ([gui/engine_adapter.py](gui/engine_adapter.py))
   - Emits `cue_levels` signal
   - Provides RMS and peak values
   - Connected to button's `_on_cue_levels` slot

### Data Flow
```
Audio Engine
    ↓
Output Process (audio_engine.py) 
    ↓ (CueLevelsEvent)
Engine Adapter (engine_adapter.py)
    ↓ (cue_levels signal)
SoundFileButton._on_cue_levels()
    ↓
AudioLevelMeter.setValue(rms_db, peak_db)
    ↓
Visual Display (paintEvent)
```

## Configuration

### Audio Levels
The button tracks audio levels in the audio engine's output process.

**Current Implementation:**
- RMS: Root Mean Square (average level)
- Peak: Maximum instantaneous level
- Update rate: ~10-50 Hz (engine dependent)
- Channels: Mixed to mono (can be extended)

**Location:** [engine/processes/output_process.py](engine/processes/output_process.py), line 310-323

### Button Integration
**Location:** [ui/widgets/sound_file_button.py](ui/widgets/sound_file_button.py)

Key methods:
- `_setup_gain_slider()` - Creates meters (line 1373)
- `_update_slider_position()` - Positions meters (line 1441)
- `_animate_slider_in/out()` - Animates meters (lines 1498-1617)
- `_on_cue_levels()` - Updates meters (line 1006)

## Performance

### Optimization Notes
- Meters only update when visible (checked in `_on_cue_levels`)
- Single animation handles all sliding widgets
- Minimal memory overhead (~2KB per button)
- No blocking operations

### Testing
Run the test suite:
```bash
python test_sound_file_button_meters.py
```

Expected output:
```
✓ Meters created and initially hidden
✓ Meters slide in with gain slider
✓ Meters slide out with gain slider
✓ Meters positioned correctly for stereo
✓ Meters positioned correctly for mono
✓ Meters update with cue_levels signals

✅ All tests passed!
```

## Troubleshooting

### Meters Not Showing
1. Check if `gain_slider_visible` is True
2. Verify `level_meter_left.isVisible()` returns True
3. Check button size (minimum ~50px height recommended)

### Meters Not Updating
1. Verify cue is playing (`current_cue_id` is set)
2. Check engine adapter connected to button
3. Verify cue_id matches in event handler
4. Check console for debug output

### Animation Stuttering
1. Check CPU usage
2. Verify event loop not blocked
3. Check if other animations running
4. Try adjusting animation duration

## Future Enhancements

### Planned
- [ ] Per-channel level processing (engine-side)
- [ ] Configurable meter width
- [ ] Theme-aware colors
- [ ] Horizontal meter option
- [ ] Level statistics (average, max, min)

### Possible
- [ ] Meter calibration
- [ ] Custom dB ranges
- [ ] Peak hold duration control
- [ ] Meter export/logging
- [ ] Integration with existing meters

## Related Files

### Core Implementation
- [ui/widgets/sound_file_button.py](ui/widgets/sound_file_button.py) - Main button widget
- [ui/widgets/AudioLevelMeter.py](ui/widgets/AudioLevelMeter.py) - Meter widget
- [gui/engine_adapter.py](gui/engine_adapter.py) - Engine interface

### Tests
- [test_sound_file_button_meters.py](test_sound_file_button_meters.py) - Unit tests

### Documentation
- [AUDIO_LEVEL_METERS_IMPLEMENTATION.md](AUDIO_LEVEL_METERS_IMPLEMENTATION.md) - Detailed implementation
- [AUDIO_LEVEL_METERS_VISUAL_GUIDE.md](AUDIO_LEVEL_METERS_VISUAL_GUIDE.md) - Visual diagrams
- [AUDIO_LEVEL_METERS_QUICK_REFERENCE.md](AUDIO_LEVEL_METERS_QUICK_REFERENCE.md) - This file

## Frequently Asked Questions

**Q: Why are meters 10 pixels wide?**
A: User specified this for compact display that doesn't overwhelm the button.

**Q: Why do both channels show the same level?**
A: Current audio engine mixes levels to mono. Can be extended for per-channel when needed.

**Q: Can I customize the meter appearance?**
A: Yes, edit colors and range in [AudioLevelMeter.py](ui/widgets/AudioLevelMeter.py).

**Q: Do meters work with mono audio?**
A: Yes, right meter is hidden for mono, left meter takes full height.

**Q: What's the performance impact?**
A: Minimal - only two additional QWidget instances per button, ~2KB memory per button.

**Q: Can I hide the meters without hiding the slider?**
A: Currently no, they're linked. Could be separated if needed.

**Q: Do meters work offline?**
A: Only when audio is playing and engine is active.

## Contact & Issues

For issues or questions about the meters:
1. Check test suite: `test_sound_file_button_meters.py`
2. Review implementation docs
3. Check console output for debug messages
4. Verify engine adapter connection
