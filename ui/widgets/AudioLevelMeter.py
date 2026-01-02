"""Audio level meter (vertical).

This widget can be updated at high frequency; to keep the GUI responsive we:
- cache static background (ticks/labels)
- precompute bar geometry/colors for current size
- coalesce rapid setValue() calls (single-shot timer) to cap repaint rate
"""

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt


class AudioLevelMeter(QtWidgets.QWidget):

    _TARGET_FPS = 60

    def __init__(self, vmin=-64, vmax=0, height=300, width=50,  *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.vmin = vmin
        self.vmax = vmax
        self.vheight = height
        self.vwidth = width

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        
        self.value = -64.0
        self.peak = -64.0
        self.setFixedHeight(self.vheight)
        self.setFixedWidth(self.vwidth)
        
        color_names = ["green", "yellow", "orange", "red"]
        colors: list[str] = []
        for i in range(64):
            l = 64 - i
            if l > 12:
                colors.append(color_names[0])
            elif 6 <= l <= 12:
                colors.append(color_names[1])
            elif 1 < l < 6:
                colors.append(color_names[2])
            else:
                colors.append(color_names[3])
        self._colors = [QtGui.QColor(name) for name in colors]
        self._colors_len = len(self._colors)

        # Cached background and geometry.
        self._bg_cache: QtGui.QPixmap | None = None
        self._bg_cache_size: QtCore.QSize | None = None
        self._geometry_cache_size: QtCore.QSize | None = None
        self._num_bars = 32
        self._left_padding = 0
        self._bar_width = 1
        self._step_size = 1.0
        self._bar_height = 1
        self._bar_y: list[int] = []
        self._bar_qcolors: list[QtGui.QColor] = []

        # Coalesce frequent updates.
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        interval_ms = max(1, int(1000 / self._TARGET_FPS))
        self._refresh_timer.setInterval(interval_ms)
        self._refresh_timer.timeout.connect(self.update)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.vwidth, self.vheight)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Invalidate caches on resize.
        self._bg_cache = None
        self._bg_cache_size = None
        self._geometry_cache_size = None

    def _ensure_geometry_cache(self, d_width: int, d_height: int) -> None:
        size = QtCore.QSize(d_width, d_height)
        if self._geometry_cache_size == size:
            return

        self._geometry_cache_size = size
        self._num_bars = 32
        self._left_padding = int(round(d_width * 0.3))
        right_padding = int(round(d_width * 0.1))
        self._bar_width = max(1, int(d_width - round(right_padding + self._left_padding)))

        self._step_size = d_height / self._num_bars
        self._bar_height = max(1, int(round(self._step_size * 0.7)))

        # Bar y positions from bottom upwards (same as original loop)
        self._bar_y = [
            int(d_height - int(round((n + 1) * self._step_size))) for n in range(self._num_bars)
        ]
        self._bar_qcolors = []
        for n in range(self._num_bars):
            color_factor = (n + 1) / self._num_bars
            idx = min(int(round(color_factor * self._colors_len)), self._colors_len - 1)
            self._bar_qcolors.append(self._colors[idx])

    def _ensure_bg_cache(self, d_width: int, d_height: int) -> None:
        size = QtCore.QSize(d_width, d_height)
        if self._bg_cache is not None and self._bg_cache_size == size:
            return

        pix = QtGui.QPixmap(size)
        pix.fill(QtGui.QColor("black"))
        painter = QtGui.QPainter(pix)
        try:
            pen = painter.pen()
            pen.setColor(QtGui.QColor("red"))
            painter.setPen(pen)

            font = painter.font()
            font.setFamily("Times")
            font.setPointSize(7)
            painter.setFont(font)
            painter.drawText(0, 4, "-")

            num_markings = 10
            remainder = 1
            remainder_scaler = remainder / 64
            remainder_px = d_height * remainder_scaler
            spacing = (d_height - (remainder_px * 3)) / num_markings
            offset = -1
            x = 0
            for i in range(num_markings):
                x -= 6
                position = int(round(spacing * (i + 1))) + offset
                number = x
                font.setPointSize(8)
                painter.setFont(font)
                if number != 0:
                    painter.drawText(3, position, "{}".format(number) + "_")
        finally:
            painter.end()

        self._bg_cache = pix
        self._bg_cache_size = size
            
    def paintEvent(self,e):

        try:
            painter = QtGui.QPainter(self)
            d_height = painter.device().height()
            d_width = painter.device().width()

            self._ensure_geometry_cache(d_width, d_height)
            self._ensure_bg_cache(d_width, d_height)

            if self._bg_cache is not None:
                painter.drawPixmap(0, 0, self._bg_cache)

            level_norm = abs(self.value - self.vmin) / (self.vmax - self.vmin)
            n_steps_to_draw = abs(int(round(level_norm * self._num_bars)))
            n_steps_to_draw = min(n_steps_to_draw, self._num_bars)

            for n in range(n_steps_to_draw):
                rect = QtCore.QRect(
                    self._left_padding,
                    self._bar_y[n],
                    self._bar_width,
                    self._bar_height,
                )
                painter.fillRect(rect, self._bar_qcolors[n])

            # Peak bar (keep original math/feel)
            peak_norm = abs(self.peak - self.vmin) / (self.vmin - self.vmax)
            peak_bar_y = int(round(d_height - abs(round(peak_norm * d_height))))
            rect2 = QtCore.QRect(
                self._left_padding,
                peak_bar_y,
                self._bar_width,
                self._bar_height,
            )
            # Map peak position to a color index like original.
            color_factor = peak_bar_y / max(1, d_height)
            idx = min(int(round(color_factor * self._colors_len)), self._colors_len - 1)
            painter.fillRect(rect2, self._colors[(self._colors_len - 1) - idx])

            painter.end()
        except Exception as err:
            print('meter error' + str(err))

       

    def _trigger_refresh(self):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()


    def setValue(self, level, peak):
        self.value = level
        self.peak = peak
        
        if level < -64:
            self.value = -64
        if peak < -64:
            self.peak = -64
            
        if level > 0:
            self.value = 0
            
        if peak > 0:
            self.peak = 0
        
        self._trigger_refresh()

    def setVmin(self, vmin):
        self.vmin = vmin
    
    def setVmax(self, vmax):
        self.vmax = vmax

    

if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    volume = AudioLevelMeter(-64,0,260,60)
    volume.setValue(-6, -2)
    volume.show()
    app.exec()