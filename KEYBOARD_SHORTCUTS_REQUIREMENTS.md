# Keyboard Shortcuts / Keyboard Capture Requirements

This project supports two keyboard-capture modes:

1. **Focused capture (Qt-only)**
   - Uses Qt key events.
   - Works when the app window has focus.
   - **Works on Windows / macOS / Linux (X11 or Wayland)** without special OS permissions.

2. **Global capture (optional)**
   - Captures keys even when the app is not focused.
   - Uses OS-dependent global backends:
     - **Windows/macOS/Linux-X11:** `pynput`
     - **Linux-Wayland:** prefers `evdev` (recommended) because Wayland frequently blocks global hooks.

The UI exposes this as the **“Global keyboard capture”** setting.

---

## Numpad vs Top-Row Digits (Critical Behavior)

This app distinguishes **numpad digits** from **top-row digits** so you can use numpad keys for bank switching.

- **Qt-focused mode:** numpad digits should appear as `Num+1` (or as “Numpad 1” in some UI formatting) and include Qt’s `KeypadModifier` bit (`0x20000000`).
- **Global mode:**
  - `pynput` identifies numpad keys via platform-specific `vk` ranges.
  - `evdev` identifies numpad keys via key names like `KEY_KP1`.

If numpad digits ever “collapse” into plain `1`, it typically means the keypad modifier bit was lost during conversion/normalization.

---

## Dependencies

### Always (focused Qt capture)
- `PySide6`

### Optional (global capture)
- `pynput`
  - Used for global capture on Windows/macOS/Linux-X11.
- `evdev` (Linux-only)
  - Used for reliable global capture on Linux Wayland (and also works on X11).

Notes:
- The app is designed to **degrade gracefully** if optional packages are missing.
- Global capture can be enabled/disabled; focused capture remains available regardless.

---

## Platform Notes

### Windows

Focused (Qt) capture:
- No special permissions.

Global (`pynput`) capture:
- Typically works without special permissions.

Numpad detection:
- Works via Qt `KeypadModifier` in focused mode and `pynput` VK range (`0x60..0x6F`) in global mode.

### macOS

Focused (Qt) capture:
- No special permissions.

Global (`pynput`) capture:
- macOS often requires user approval for global input monitoring.
- Depending on macOS version and packaging, you may need to enable permissions under:
  - **System Settings → Privacy & Security → Accessibility**
  - and/or **Input Monitoring**

If permissions are not granted:
- `pynput` may fail to start or may not receive events.
- The app will still work in focused mode.

Numpad detection:
- `pynput` uses common Apple virtual keycodes for keypad digits: `{82,83,84,85,86,87,88,89,91,92}`.

### Linux (X11)

Focused (Qt) capture:
- Works without special permissions.

Global (`pynput`) capture:
- Typically works if an X server is present and accessible.

If `pynput` doesn’t work:
- Use focused mode, or consider `evdev` (with permissions).

### Linux (Wayland)

Focused (Qt) capture:
- Works without special permissions.

Global capture:
- **Wayland commonly blocks global key capture** for security reasons.
- The recommended approach is **`evdev`**, which reads kernel input events.

`evdev` requirements:
- Install package: `pip install evdev`
- Ensure the user running the app can read from `/dev/input/event*`.
  - On many distros this requires being in the `input` group and/or adding a udev rule.

Symptoms and typical causes:
- “evdev found no readable keyboard devices” → permission issue (or running in a container without access to `/dev/input`).
- `pynput` starts but doesn’t receive keys under Wayland → expected on many compositors.

---

## Quick Verification (Recommended)

Use the built-in diagnostic harness:

- Run: `venv\\Scripts\\python.exe pynput_test.py`

Press keys in this order:
1. Numpad `1` then top-row `1`
2. Numpad `2` then top-row `2`

Expected terminal output pattern:
- Numpad:
  - `QT ... seq='Num+1' ... keypadMod=True ... mods=536870912`
  - `PYNPUT ... vk=97` (for numpad 1 on Windows)
- Top-row:
  - `QT ... seq='1' ... keypadMod=False ... mods=0`
  - `PYNPUT ... vk=49`

If those expectations hold, the app should be able to bind numpad and top-row digits separately.

---

## Troubleshooting Checklist

- If global capture doesn’t work:
  - Linux Wayland: install/configure `evdev` permissions.
  - macOS: grant Accessibility/Input Monitoring permissions.
  - Any OS: disable “Global keyboard capture” to confirm focused mode still works.

- If numpad can’t be distinguished:
  - Re-run `pynput_test.py` and confirm `mods=536870912` and `keypadMod=True` for numpad in the **QT** line.
  - Confirm your saved binding includes the keypad modifier bit (UI should display `Num+...` / `Numpad ...`).
