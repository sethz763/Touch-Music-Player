from __future__ import annotations

import math
import os
import queue
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RenderMsg:
    key: int
    text: str
    active_level: float
    icon_path: Optional[str] = None
    bg_image_path: Optional[str] = None
    bg_rgb: Optional[tuple[int, int, int]] = None
    fg_rgb: Optional[tuple[int, int, int]] = None
    corner_text: str = ""


# -----------------------------------------------------------------------------
# Minimal bitmap font (5x7) to avoid PIL.ImageFont / freetype usage.
# -----------------------------------------------------------------------------

_FONT_5X7: dict[str, list[int]] = {
    " ": [0, 0, 0, 0, 0, 0, 0],
    "?": [0b01110, 0b10001, 0b00010, 0b00100, 0b00100, 0b00000, 0b00100],
    "-": [0, 0, 0, 0b11111, 0, 0, 0],
    "_": [0, 0, 0, 0, 0, 0, 0b11111],
    ".": [0, 0, 0, 0, 0, 0b00100, 0b00100],
    "/": [0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0, 0],
    ":": [0, 0b00100, 0b00100, 0, 0b00100, 0b00100, 0],
    "0": [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
    "1": [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    "2": [0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111],
    "3": [0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110],
    "4": [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
    "5": [0b11111, 0b10000, 0b10000, 0b11110, 0b00001, 0b00001, 0b11110],
    "6": [0b01110, 0b10000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
    "7": [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
    "8": [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
    "9": [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00001, 0b01110],
    "A": [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
    "B": [0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110],
    "C": [0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110],
    "D": [0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110],
    "E": [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111],
    "F": [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000],
    "G": [0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01110],
    "H": [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
    "I": [0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    "J": [0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100],
    "K": [0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001],
    "L": [0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111],
    "M": [0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001],
    "N": [0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001],
    "O": [0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
    "P": [0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000],
    "Q": [0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101],
    "R": [0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001],
    "S": [0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110],
    "T": [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100],
    "U": [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
    "V": [0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b01010, 0b00100],
    "W": [0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001],
    "X": [0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b01010, 0b10001],
    "Y": [0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100],
    "Z": [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111],
}


def _normalize_text(s: str) -> str:
    # Keep it deterministic and ASCII-ish for bitmap font.
    out = []
    for ch in (s or ""):
        if ch == "\n":
            out.append("\n")
            continue
        if ord(ch) < 32:
            continue
        # Preserve common punctuation and digits; uppercase letters.
        c = ch.upper()
        if c not in _FONT_5X7:
            if "A" <= c <= "Z" or "0" <= c <= "9":
                pass
            else:
                c = "?"
        out.append(c)
    return "".join(out)


def _wrap_bitmap_lines(text: str, max_cols: int, max_lines: int) -> list[str]:
    raw = _normalize_text(text).strip()
    if not raw:
        return []

    max_cols = max(1, int(max_cols))
    max_lines = max(1, int(max_lines))

    # Respect explicit newlines.
    hard_lines = raw.split("\n")

    lines: list[str] = []
    for hl in hard_lines:
        hl = hl.strip()
        if not hl:
            if lines:
                lines.append("")
            continue
        words = hl.split() if hl else []
        cur = ""
        for w in words:
            if not w:
                continue

            # Hard-wrap long tokens (no spaces / long file names).
            if len(w) > max_cols:
                if cur:
                    lines.append(cur)
                    cur = ""
                    if len(lines) >= max_lines:
                        return lines[:max_lines]

                for i in range(0, len(w), max_cols):
                    lines.append(w[i : i + max_cols])
                    if len(lines) >= max_lines:
                        return lines[:max_lines]
                continue

            if not cur:
                cur = w
            else:
                cand = cur + " " + w
                if len(cand) <= max_cols:
                    cur = cand
                else:
                    lines.append(cur)
                    cur = w
            if len(lines) >= max_lines:
                return lines[:max_lines]

        if cur:
            lines.append(cur)
            if len(lines) >= max_lines:
                return lines[:max_lines]

    return lines[:max_lines]


def _draw_bitmap_text_rgb(arr, x0: int, y0: int, lines: list[str], rgb: tuple[int, int, int], *, scale: int = 2) -> None:
    import numpy as np

    if arr is None:
        return
    if scale < 1:
        scale = 1

    h, w, _ = arr.shape
    ch_w = 6 * scale  # 5 px + 1 px spacing
    ch_h = 8 * scale  # 7 px + 1 px spacing

    y = int(y0)
    for line in lines:
        x = int(x0)
        for ch in line:
            glyph = _FONT_5X7.get(ch) or _FONT_5X7.get("?")
            if glyph is None:
                x += ch_w
                continue
            for gy in range(7):
                row = int(glyph[gy])
                if row == 0:
                    continue
                for gx in range(5):
                    if (row >> (4 - gx)) & 1:
                        px0 = x + gx * scale
                        py0 = y + gy * scale
                        px1 = px0 + scale
                        py1 = py0 + scale
                        if px0 >= w or py0 >= h or px1 <= 0 or py1 <= 0:
                            continue
                        arr[max(0, py0) : min(h, py1), max(0, px0) : min(w, px1), 0] = int(rgb[0])
                        arr[max(0, py0) : min(h, py1), max(0, px0) : min(w, px1), 1] = int(rgb[1])
                        arr[max(0, py0) : min(h, py1), max(0, px0) : min(w, px1), 2] = int(rgb[2])
            x += ch_w
        y += ch_h


def _safe_resize_rgba(pil_rgba, size: tuple[int, int]):
    # Avoid PIL.Image.resize (can access-violate on some Windows builds).
    try:
        import numpy as np
        from PIL import Image

        tw = max(1, int(size[0]))
        th = max(1, int(size[1]))
        if getattr(pil_rgba, "size", None) == (tw, th):
            return pil_rgba

        src = pil_rgba
        if getattr(src, "mode", None) != "RGBA":
            src = src.convert("RGBA")
        try:
            src.load()
        except Exception:
            pass

        arr = np.asarray(src, dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[2] != 4:
            return None

        sh, sw, _ = arr.shape
        if sh <= 0 or sw <= 0:
            return None

        ys = np.rint(np.linspace(0, sh - 1, th)).astype(np.intp)
        xs = np.rint(np.linspace(0, sw - 1, tw)).astype(np.intp)
        out = arr[np.ix_(ys, xs)]
        return Image.fromarray(out, mode="RGBA")
    except Exception:
        return None


def _render_key_image(msg: RenderMsg, key_size: tuple[int, int], icon_cache: dict, bg_cache: dict):
    from PIL import Image, ImageDraw, ImageFont

    w, h = int(key_size[0]), int(key_size[1])
    level = float(max(0.0, min(1.0, float(msg.active_level))))

    fg = (255, 255, 255)
    if msg.fg_rgb is not None:
        try:
            fg = (int(msg.fg_rgb[0]), int(msg.fg_rgb[1]), int(msg.fg_rgb[2]))
        except Exception:
            fg = (255, 255, 255)

    if msg.bg_rgb is None:
        bg = int(20 + level * 140)
        base_rgb = (bg, bg, bg)
    else:
        r, g, b = (int(msg.bg_rgb[0]), int(msg.bg_rgb[1]), int(msg.bg_rgb[2]))
        lift = int(60 * level)
        base_rgb = (min(255, r + lift), min(255, g + lift), min(255, b + lift))

    img = Image.new("RGBA", (w, h), (int(base_rgb[0]), int(base_rgb[1]), int(base_rgb[2]), 255))

    def _load_rgba_cached(path: str, cache: dict):
        cached = cache.get(path)
        if cached is not None:
            return cached
        try:
            with Image.open(path) as im:
                rgba = im.convert("RGBA")
                rgba.load()
                rgba = rgba.copy()
            cache[path] = rgba
            return rgba
        except Exception:
            return None

    # Background image.
    if msg.bg_image_path:
        try:
            bg = _load_rgba_cached(str(msg.bg_image_path), bg_cache)
            if bg is not None:
                scale = max(w / bg.width, h / bg.height)
                new_size = (max(1, int(bg.width * scale)), max(1, int(bg.height * scale)))
                bg_resized = _safe_resize_rgba(bg, new_size)
                if bg_resized is not None:
                    x = (bg_resized.width - w) // 2
                    y = (bg_resized.height - h) // 2
                    bg_cropped = bg_resized.crop((int(x), int(y), int(x + w), int(y + h)))
                    img.alpha_composite(bg_cropped, (0, 0))

                    signed = (level - 0.5) * 2.0
                    if signed > 0:
                        a = int(80 * signed)
                        if a > 0:
                            img.alpha_composite(Image.new("RGBA", (w, h), (255, 255, 255, a)), (0, 0))
                    else:
                        a = int(80 * (-signed))
                        if a > 0:
                            img.alpha_composite(Image.new("RGBA", (w, h), (0, 0, 0, a)), (0, 0))
        except Exception:
            pass

    # Icon: centered (but do NOT early-return; we still may draw text overlay).
    if msg.icon_path:
        try:
            icon = _load_rgba_cached(str(msg.icon_path), icon_cache)
            if icon is not None:
                pad = 8
                max_w = max(1, w - pad * 2)
                max_h = max(1, h - pad * 2)
                scale = min(max_w / icon.width, max_h / icon.height)
                new_size = (max(1, int(icon.width * scale)), max(1, int(icon.height * scale)))
                icon_resized = _safe_resize_rgba(icon, new_size)
                if icon_resized is not None:
                    x = (w - icon_resized.width) // 2
                    y = (h - icon_resized.height) // 2
                    img.alpha_composite(icon_resized, (int(x), int(y)))
        except Exception:
            pass

    # Text rendering (preferred): Pillow font rendering in the worker.
    # Important: avoid ImageDraw.textbbox()/ImageFont.getbbox() due to prior Windows AVs.
    # Instead, rasterize to a temp image and compute bounds via numpy.
    raw_text = str(msg.text or "")
    corner_text = str(msg.corner_text or "")

    def _pick_font_path() -> Optional[str]:
        try:
            env_path = os.environ.get("STEPD_STREAMDECK_FONT_PATH")
            if env_path and os.path.isfile(env_path):
                return env_path
        except Exception:
            pass

        # Windows common fonts (best-effort).
        candidates = [
            r"C:\\Windows\\Fonts\\segoeui.ttf",
            r"C:\\Windows\\Fonts\\arial.ttf",
            r"C:\\Windows\\Fonts\\tahoma.ttf",
            r"C:\\Windows\\Fonts\\consola.ttf",
        ]
        for p in candidates:
            try:
                if os.path.isfile(p):
                    return p
            except Exception:
                continue
        return None

    def _load_font_cached(size: int, font_path: Optional[str], cache: dict) -> Optional[object]:
        size = int(size)
        key = (str(font_path or ""), size)
        cached = cache.get(key)
        if cached is not None:
            return cached
        try:
            if font_path:
                f = ImageFont.truetype(font_path, size)
            else:
                # Fallback to default bitmap font.
                f = ImageFont.load_default()
            cache[key] = f
            return f
        except Exception:
            try:
                f = ImageFont.load_default()
                cache[key] = f
                return f
            except Exception:
                return None

    # Lightweight per-process font cache.
    font_cache = icon_cache.setdefault("__font_cache__", {})
    font_path = _pick_font_path()

    def _raster_text_alpha(text: str, font_obj, *, max_w: int, max_h: int):
        """Return (alpha_array, w, h) for the tight bounding box of rendered text."""
        if not text:
            return None
        try:
            import numpy as np

            # Render to a generous scratch canvas, then compute tight bounds.
            scratch_w = max(64, int(max_w) * 2)
            scratch_h = max(64, int(max_h) * 2)
            scratch = Image.new("L", (scratch_w, scratch_h), 0)
            d = ImageDraw.Draw(scratch)
            # Use multiline_text, but do NOT ask PIL for bbox.
            d.multiline_text((0, 0), text, fill=255, font=font_obj, spacing=2, align="center")

            a = np.asarray(scratch, dtype=np.uint8)
            ys, xs = np.where(a > 0)
            if ys.size == 0 or xs.size == 0:
                return None
            y0 = int(ys.min())
            y1 = int(ys.max()) + 1
            x0 = int(xs.min())
            x1 = int(xs.max()) + 1
            cropped = a[y0:y1, x0:x1]
            return cropped
        except Exception:
            return None

    def _paste_alpha_as_rgb(base_rgb_img, alpha, *, x: int, y: int, rgb: tuple[int, int, int]):
        try:
            # Convert alpha mask into an RGBA image with the desired color.
            mask = Image.fromarray(alpha, mode="L")
            col = Image.new("RGBA", mask.size, (int(rgb[0]), int(rgb[1]), int(rgb[2]), 255))
            col.putalpha(mask)
            base_rgb_img.alpha_composite(col, (int(x), int(y)))
            return True
        except Exception:
            return False

    # Attempt PIL-font text draw if there is any text/corner to draw.
    if raw_text.strip() or corner_text.strip():
        try:
            import numpy as np

            pad = 6
            top_reserved = 0
            if corner_text.strip():
                # Reserve a small amount of space; exact measurement is avoided.
                top_reserved = 14

            avail_w = max(1, w - pad * 2)
            avail_h = max(1, h - pad * 2 - top_reserved)

            # Normalize and wrap text without relying on font measurements.
            # This explicitly handles long labels with no spaces.
            max_cols = max(6, int(avail_w // 10))
            max_lines = 4

            def _wrap_text_hard(raw: str) -> str:
                raw = (raw or "").strip()
                if not raw:
                    return ""

                out_lines: list[str] = []

                for hard_ln in raw.split("\n"):
                    hard_ln = (hard_ln or "").strip()
                    if not hard_ln:
                        if out_lines:
                            out_lines.append("")
                        if len(out_lines) >= max_lines:
                            break
                        continue

                    words = hard_ln.split() if hard_ln else []
                    cur = ""
                    for w0 in words:
                        if not w0:
                            continue

                        # Hard-wrap tokens that exceed the column budget.
                        if len(w0) > max_cols:
                            if cur:
                                out_lines.append(cur)
                                cur = ""
                                if len(out_lines) >= max_lines:
                                    break
                            for i in range(0, len(w0), max_cols):
                                out_lines.append(w0[i : i + max_cols])
                                if len(out_lines) >= max_lines:
                                    break
                            if len(out_lines) >= max_lines:
                                break
                            continue

                        cand = (cur + " " + w0).strip() if cur else w0
                        if len(cand) <= max_cols:
                            cur = cand
                        else:
                            if cur:
                                out_lines.append(cur)
                                if len(out_lines) >= max_lines:
                                    break
                            cur = w0
                        if len(out_lines) >= max_lines:
                            break

                    if len(out_lines) >= max_lines:
                        break
                    if cur:
                        out_lines.append(cur)
                        if len(out_lines) >= max_lines:
                            break

                return "\n".join(out_lines[:max_lines])

            text_norm = _wrap_text_hard(raw_text)

            # Choose a font size by trial rasterization (largest that fits).
            best_alpha = None
            best_size = None
            for size in (28, 24, 22, 20, 18, 16, 14, 12, 10):
                f = _load_font_cached(size, font_path, font_cache)
                if f is None:
                    continue
                alpha = _raster_text_alpha(text_norm, f, max_w=avail_w, max_h=avail_h)
                if alpha is None:
                    continue
                ah, aw = int(alpha.shape[0]), int(alpha.shape[1])
                if aw <= avail_w and ah <= avail_h:
                    best_alpha = alpha
                    best_size = size
                    break

            if best_alpha is None:
                # Last resort: render at smallest.
                f = _load_font_cached(10, font_path, font_cache)
                if f is not None:
                    best_alpha = _raster_text_alpha(text_norm, f, max_w=avail_w, max_h=avail_h)
                    best_size = 10

            if best_alpha is not None:
                ah, aw = int(best_alpha.shape[0]), int(best_alpha.shape[1])
                x0 = pad + max(0, (avail_w - aw) // 2)
                y0 = pad + top_reserved + max(0, (avail_h - ah) // 2)
                _paste_alpha_as_rgb(img, best_alpha, x=x0, y=y0, rgb=fg)

            # Corner label: small and simple; don't measure, just draw. 
            # It is positioned at the top-left corner, with a small left margin of 10px
            if corner_text.strip():
                cf = _load_font_cached(12, font_path, font_cache)
                if cf is not None:
                    calpha = _raster_text_alpha(corner_text.strip()[:12], cf, max_w=w, max_h=h)
                    if calpha is not None:
                        _paste_alpha_as_rgb(img, calpha, x=10, y=2, rgb=(255, 255, 255))

            return img.convert("RGB")
        except Exception:
            # Fall through to bitmap font below.
            pass

    # Text rendering: bitmap font (no PIL.ImageFont).
    try:
        import numpy as np

        arr = np.asarray(img.convert("RGB"), dtype=np.uint8)

        pad = 6
        # Primary label scale: tuned for 72x72 keys.
        scale = 2
        ch_w = 6 * scale
        ch_h = 8 * scale
        max_cols = max(1, (w - pad * 2) // ch_w)
        max_lines = 4

        # Corner label (optional) reserves space at top.
        top_reserved = 0
        corner = _normalize_text(msg.corner_text).strip()
        if corner:
            corner_scale = 1
            corner_h = 8 * corner_scale
            top_reserved = corner_h + 2
            # draw corner at top-left
            _draw_bitmap_text_rgb(arr, 2, 2, [corner[:12]], (255, 255, 255), scale=corner_scale)

        lines = _wrap_bitmap_lines(msg.text, max_cols=max_cols, max_lines=max_lines)
        if lines:
            total_h = len(lines) * ch_h + max(0, (len(lines) - 1) * 2)
            y0 = pad + top_reserved + max(0, (h - pad - (pad + top_reserved) - total_h) // 2)
            for li, ln in enumerate(lines):
                ln = ln[:max_cols]
                x0 = pad + max(0, (w - pad * 2 - len(ln) * ch_w) // 2)
                _draw_bitmap_text_rgb(arr, x0, y0 + li * (ch_h + 2), [ln], fg, scale=scale)

        out = Image.fromarray(arr, mode="RGB")
        return out
    except Exception:
        return img.convert("RGB")


def run_streamdeck_worker(cmd_q, evt_q, stop_event) -> None:
    """Worker process that owns StreamDeck HID + rendering.

    Events pushed to evt_q:
      - {"type": "connected", "value": True, "key_size": (w,h)}
      - {"type": "connected", "value": False}
      - {"type": "key", "key": int}
      - {"type": "error", "detail": str}

    Commands read from cmd_q:
      - {"type": "render", ... RenderMsg fields ...}
      - {"type": "shutdown"}
    """

    deck = None
    key_size = (72, 72)
    last_connected = False

    icon_cache: dict[str, object] = {}
    bg_cache: dict[str, object] = {}

    def _close(reason: str) -> None:
        nonlocal deck, last_connected
        try:
            if deck is not None:
                try:
                    deck.reset()
                except Exception:
                    pass
                try:
                    deck.close()
                except Exception:
                    pass
        finally:
            deck = None
            if last_connected:
                last_connected = False
                try:
                    evt_q.put_nowait({"type": "connected", "value": False, "reason": reason})
                except Exception:
                    pass

    def _try_open_first_deck():
        try:
            from StreamDeck.DeviceManager import DeviceManager
        except Exception:
            return None
        try:
            decks = DeviceManager().enumerate() or []
        except Exception:
            return None
        if not decks:
            return None
        d = decks[0]
        try:
            d.open()
        except Exception:
            return None
        try:
            d.reset()
        except Exception:
            pass
        try:
            d.set_brightness(50)
        except Exception:
            pass
        try:
            fmt = d.key_image_format() or {}
            size = fmt.get("size")
            if isinstance(size, (list, tuple)) and len(size) == 2:
                return d, (int(size[0]), int(size[1]))
        except Exception:
            pass
        return d, (72, 72)

    def _on_key_change(_deck, key: int, state: bool) -> None:
        if not bool(state):
            return
        try:
            evt_q.put_nowait({"type": "key", "key": int(key)})
        except Exception:
            return

    # Main loop
    while not stop_event.is_set():
        # Ensure deck open.
        if deck is None:
            try:
                opened = _try_open_first_deck()
                if opened is not None:
                    deck, key_size = opened
                    try:
                        deck.set_key_callback(_on_key_change)
                    except Exception:
                        _close("set_key_callback_failed")
                    else:
                        last_connected = True
                        try:
                            evt_q.put_nowait({"type": "connected", "value": True, "key_size": key_size})
                        except Exception:
                            pass
            except Exception as e:
                try:
                    evt_q.put_nowait({"type": "error", "detail": f"open_failed: {type(e).__name__}: {e}"})
                except Exception:
                    pass

        # Drain commands.
        pending: dict[int, RenderMsg] = {}
        drained = 0
        while drained < 256:
            drained += 1
            try:
                cmd = cmd_q.get(timeout=1.0 / 60.0)
            except queue.Empty:
                break
            except Exception:
                break

            try:
                if not isinstance(cmd, dict):
                    continue
                if cmd.get("type") == "shutdown":
                    stop_event.set()
                    break
                if cmd.get("type") != "render":
                    continue

                msg = RenderMsg(
                    key=int(cmd.get("key")),
                    text=str(cmd.get("text") or ""),
                    active_level=float(cmd.get("active_level") or 0.0),
                    icon_path=cmd.get("icon_path"),
                    bg_image_path=cmd.get("bg_image_path"),
                    bg_rgb=cmd.get("bg_rgb"),
                    fg_rgb=cmd.get("fg_rgb"),
                    corner_text=str(cmd.get("corner_text") or ""),
                )
                if msg.key >= 0:
                    pending[int(msg.key)] = msg
            except Exception:
                continue

        if stop_event.is_set():
            break

        if deck is None:
            # No device yet; keep looping.
            time.sleep(0.1)
            continue

        # Render+write newest per key.
        def _priority(k: int) -> int:
            if k in (26, 27, 28):
                return 0
            if k in (24, 25, 29, 30, 31):
                return 1
            return 2

        for key in sorted(pending.keys(), key=_priority):
            msg = pending.get(key)
            if msg is None:
                continue
            try:
                img = _render_key_image(msg, key_size=key_size, icon_cache=icon_cache, bg_cache=bg_cache)
                try:
                    from StreamDeck.ImageHelpers import PILHelper
                except Exception as e:
                    raise RuntimeError(f"PILHelper import failed: {e}")
                native = PILHelper.to_native_format(deck, img)
                deck.set_key_image(int(msg.key), native)
            except Exception as e:
                try:
                    evt_q.put_nowait({"type": "error", "detail": f"render_or_write_failed: {type(e).__name__}: {e}"})
                except Exception:
                    pass
                _close("render_or_write_failed")
                break

    _close("shutdown")
