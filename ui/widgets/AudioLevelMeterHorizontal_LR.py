"""Audio level meter (horizontal).

This widget is repainted frequently; to keep the GUI smooth at large sizes we:
- cache static background (ticks/labels) and bar geometry on resize
- coalesce rapid setValue() calls (single-shot timer) to limit repaint rate
"""

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt


class AudioLevelMeterHorizontal(QtWidgets.QWidget):
    _TARGET_FPS = 60

    def __init__(self, vmin=0, vmax=0, height=50, width=200, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.vmin = vmin
        self.vmax = vmax
        self.vheight = height
        self.vwidth = width

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        self.level = -64.0  # dB
        self.peak = -64.0  # dB

        self.setFixedHeight(self.vheight)
        self.setMinimumWidth(self.vwidth)

        # Precompute palette (as QColor objects) once.
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

        # Cached background (ticks/labels) and cached bar geometry.
        self._bg_cache: QtGui.QPixmap | None = None
        self._bg_cache_size: QtCore.QSize | None = None
        self._geometry_cache_size: QtCore.QSize | None = None
        self._num_bars = 1
        self._bar_width = 1
        self._bar_spacer = 1
        self._top_padding = 0
        self._bar_height = 1
        self._bar_x: list[int] = []
        self._bar_qcolors: list[QtGui.QColor] = []

        # Coalesce frequent updates to avoid repaint storms.
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
        self._num_bars = max(1, int(d_width / 8))

        step_size = d_width / self._num_bars
        self._bar_width = max(1, int(round(step_size * 0.6)))
        self._bar_spacer = max(0, int(round(step_size - self._bar_width)))

        self._top_padding = int(round(d_height * 0.2))
        bottom_padding = int(round(d_height * 0.3))
        self._bar_height = max(1, int(d_height - round(self._top_padding + bottom_padding)))

        stride = self._bar_width + self._bar_spacer
        self._bar_x = [int(round(n * stride)) for n in range(self._num_bars)]
        # Pre-map each bar index to a QColor so paintEvent is tight.
        self._bar_qcolors = []
        for n in range(self._num_bars):
            color_factor = n / self._num_bars
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
            font.setPointSize(5)
            painter.setFont(font)

            painter.drawText(1, d_height - 12, "|")
            painter.drawText(d_width - 4, d_height - 12, "|")

            # Scale ticks/labels (same logic as original)
            remainder = 1
            start = int(round(d_width * (remainder / 64)))
            for i in range(-66, 1, 6):
                factor = (remainder + abs(i)) / 64
                position = start + start + 2 + int(round(d_width * factor))
                number = 0 - (60 - abs(i))
                font.setPointSize(5)
                painter.setFont(font)
                painter.drawText(position, d_height - 12, "|")
                font.setPointSize(10)
                painter.setFont(font)
                painter.drawText(position - 5, d_height, "{}".format(number))
        finally:
            painter.end()

        self._bg_cache = pix
        self._bg_cache_size = size

    def paintEvent(self, e):
        painter = None
        try:
            painter = QtGui.QPainter(self)
            d_height = painter.device().height()
            d_width = painter.device().width()

            self._ensure_geometry_cache(d_width, d_height)
            self._ensure_bg_cache(d_width, d_height)

            if self._bg_cache is not None:
                painter.drawPixmap(0, 0, self._bg_cache)

            # Convert dB -> bar counts
            level_norm = (self.level - self.vmin) / (self.vmin - self.vmax)
            peak_norm = (self.peak - self.vmin) / (self.vmin - self.vmax)
            n_steps_to_draw = abs(int(round(level_norm * self._num_bars)))
            n_steps_to_draw = min(n_steps_to_draw, self._num_bars)

            peak_bar = abs(int(round(peak_norm * self._num_bars)))
            peak_bar = max(0, min(peak_bar, self._num_bars - 1))

            # Draw bars
            for n in range(n_steps_to_draw):
                rect = QtCore.QRect(
                    self._bar_x[n],
                    self._top_padding,
                    self._bar_width,
                    self._bar_height,
                )
                painter.fillRect(rect, self._bar_qcolors[n])

            # Draw peak bar
            peak_x = int(round((peak_bar - 0.6) * (self._bar_width + self._bar_spacer)))
            rect = QtCore.QRect(
                peak_x,
                self._top_padding,
                self._bar_width,
                self._bar_height,
            )
            painter.fillRect(rect, self._bar_qcolors[peak_bar])
        except Exception as err:
            print("meter error" + str(err))
        finally:
            try:
                if painter is not None:
                    painter.end()
            except Exception:
                pass

       

    def _trigger_refresh(self):
        # Coalesce frequent value changes into a single repaint.
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()


    def setValue(self, level:float = -64, peak:float = -64):
        self.level = round(level,3)
        self.peak =round(peak, 3)
        if level < -64:
            self.level = -64
        if peak < -64:
            self.peak = -64
        self._trigger_refresh()

    def setVmin(self, vmin):
        self.vmin = vmin
    
    def setVmax(self, vmax):
        self.vmax = vmax

    



# app = QtWidgets.QApplication([])
# volume = AudioLevelMeterHorizontal(-64,0,70,350)
# volume.setValue(-54, -12)
# volume.show()
# app.exec()