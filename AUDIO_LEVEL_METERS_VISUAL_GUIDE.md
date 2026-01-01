# AudioLevelMeter Visual Layout

## Stereo Layout (2 Channels)

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  Button Content (text, play indicator)                     │
│                                                             │
│                                          ┌─┬──────────────┐│
│                                          │L│              ││
│                                          │▌│  Gain        ││
│                                          │▐│  Slider      ││
│                    ┌─┬──────────────┐    │▌│ -64 to +20dB││
│                    │L│              │    │▐│              ││
│                    │▌│              │    │ │              ││
│   Meters          │▐│              │    │ │              ││
│  (visible when    │▌│ (Stereo Pair)│    │ │              ││
│   slider shown)   │▐│              │    │ │  Reset ────┐ ││
│                    │ │              │    │ │  +0.0 dB   │ ││
│                    │ │              │    │ │            │ ││
│                    │ │              │    └─┴──────────────┘│
│                    │R│              │      │
│                    │▌│              │      │
│                    │▐│              │      │
│                    │▌│              │   10px
│                    │▐│              │
│                    └─┴──────────────┘
│                       10px each       30px
└─────────────────────────────────────────────────────────────┘

L = Left channel meter
R = Right channel meter
▌ = Meter bars (animated)
```

## Mono Layout (1 Channel)

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  Button Content (text, play indicator)                     │
│                                                             │
│                                          ┌─┬──────────────┐│
│                                          │ │              ││
│                                          │▌│  Gain        ││
│                                          │▐│  Slider      ││
│                    ┌─┬──────────────┐    │▌│ -64 to +20dB││
│                    │ │              │    │▐│              ││
│                    │▌│              │    │ │              ││
│   Meter           │▐│  (Mono)      │    │ │              ││
│  (visible when    │▌│              │    │ │  Reset ────┐ ││
│   slider shown)   │▐│              │    │ │  +0.0 dB   │ ││
│                    │ │              │    │ │            │ ││
│                    │ │              │    └─┴──────────────┘│
│                    │ │              │      │
│                    │ │              │      │
│                    │ │              │   10px
│                    └─┴──────────────┘
│                       10px
└─────────────────────────────────────────────────────────────┘
```

## Animation Sequence

### Show Animation (Swipe Left)
```
Frame 0 (Hidden)          Frame 1 (In Progress)      Frame 2 (Visible)
────────────────         ──────────────────────      ────────────────
                              ┌─┐                    ┌─┐┌─────┐
                              │░│                    │L││    │
            ┌─┬────────────┐   │░│  ┌─┐┌─────┐     │R││Gain│
            │ │            │   │░│  │▌││     │      │ ││    │
            │▌│            │   │░│  │▐││     │      │ ││Sld.│
            │▐│            │   │░│  │ ││Reset│      │ ││    │
      ────> │ │            │   │░│  │ ││+0.0 │  ──> │ ││    │
                    ↑          ↑        ↑                ↑
            Getting wider    Sliding in from right


Duration: 300ms
Easing: OutCubic (starts fast, slows down)
```

### Hide Animation (Swipe Right)
```
Frame 0 (Visible)         Frame 1 (In Progress)      Frame 2 (Hidden)
────────────────         ──────────────────────      ────────────────
┌─┐┌─────┐                                               ┌─┐
│L││    │                    ┌─┐                        │░│
│R││Gain│    ┌─┐┌─────┐      │░│  ┌─┬────────────┐     │░│
│ ││    │    │▌││     │      │░│  │ │            │     │░│
│ ││Sld.│    │▐││     │      │░│  │▌│            │  ──>
│ ││    │    │ ││Reset│      │░│  │▐│            │
  ↑        ↑        ↑          ↑        ↑
                    Sliding out to right

Duration: 300ms
Easing: InCubic (starts slow, accelerates)
```

## Level Meter Display

### Meter Visualization
```
Peak Hold Line (red)
       ↓
    ┌──────┐
    │  ▌▌  │ ← Current Level
    │  ▌▌  │
    │  ▌▌  │
    │ ▌▌▌▌ │ ← Color changes with level
    │ ▌▌▌▌ │   Green  (-64 to -12 dB)
    │▌▌▌▌▌▌│   Yellow (-12 to -6 dB)
    │▌▌▌▌▌▌│   Orange (-6 to -1 dB)
    │▌▌▌▌▌▌│   Red    (-1 to 0 dB)
    └──────┘

Width: 10px (very narrow)
Height: Scales with button height
Update: Real-time from audio engine
```

## Interaction States

### Hidden (Default)
```
┌────────────────────┐
│ Button Content     │
│ (No meters)        │
└────────────────────┘
```

### Partially Visible (During Animation)
```
┌────────────────────────────┐
│ Button Content  │░│ ┌─────│
│ (Meters sliding in)  │Gain│
│ ░░░ = Animating     │Sldr│
└────────────────────┘└─────┘
```

### Fully Visible
```
┌────────────────────────────────────┐
│ Button Content ┌─┐┌─────────────┐  │
│           L │▌│ │ Gain Slider  │  │
│           R │▌│ │   -10 dB     │  │
│             │▌│ │   Reset ──┐  │  │
│             │▌│ │   +0.0 dB │  │  │
│             └─┘ └─────────────┘  │
└────────────────────────────────────┘
```

## Responsive Behavior

### Large Button (200x100px)
```
┌──────────────────────────────────────┐
│  Audio Clip Name                     │
│  Duration / Status                   │
│  ┌─┬──────────────────────────────┐  │
│  │L│                              │  │
│  │▌│      Gain Slider             │  │
│  │▐│     -20 dB / +10 dB          │  │
│  │ │                              │  │
│  │R│      Reset         +0.0 dB   │  │
│  │▌│                              │  │
│  └─┴──────────────────────────────┘  │
└──────────────────────────────────────┘
```

### Small Button (100x80px)
```
┌──────────────────────┐
│  Audio Clip          │
│  ┌─┬────────────┐    │
│  │L│            │    │
│  │▌│ Slider     │    │
│  │R│  Reset     │    │
│  │▌│            │    │
│  └─┴────────────┘    │
└──────────────────────┘
```

## Meter Values and Scale

### dB Scale Mapping
```
 0 dB ├─────────────────── Full scale / Clipping
      │
-1 dB ├─────────────────── Red zone
      │
-6 dB ├─────────────────── Orange zone
      │
-12 dB├─────────────────── Yellow zone
      │
-24 dB├─────────────────── Green zone (good level)
      │
-40 dB├─────────────────── Quiet
      │
-64 dB├─────────────────── Silence / Noise floor
```

### Linear to dB Conversion
```
Linear RMS → dB Conversion Formula:
dB = 20 × log₁₀(RMS_linear)

Examples:
• RMS = 1.0    →   0 dB  (Maximum)
• RMS = 0.1    → -20 dB
• RMS = 0.01   → -40 dB
• RMS = 0.001  → -60 dB
• RMS = 0.0    → -64 dB (Silence)
```

## Performance Characteristics

### Animation Performance
- Duration: 300ms smooth fade
- FPS: 60fps (16.67ms per frame)
- Total frames: ~18 frames
- GPU acceleration: Qt handles via GraphicsView

### Update Frequency
- Cue levels: ~10-50 Hz (engine dependent)
- Meter refresh: Only when visible
- CPU impact: Minimal (only 2 QWidget updates per event)

### Memory Usage
- Per button overhead: ~2KB (two meter instances)
- Animation overhead: ~1KB (temporary during animation)
- Total for 10 buttons: ~20-30KB additional
