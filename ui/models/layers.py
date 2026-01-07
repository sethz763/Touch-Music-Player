from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter


@dataclass
class Layer:
    name: str = "Layer"
    visible: bool = True
    opacity: float = 1.0  # 0..1
    position: QPointF = field(default_factory=lambda: QPointF(0.0, 0.0))
    z_index: int = 0

    def render(self, painter: QPainter) -> None:
        raise NotImplementedError


@dataclass
class RasterLayer(Layer):
    image: QImage = field(default_factory=lambda: QImage())

    def ensure_size(self, size: int) -> None:
        if size <= 0:
            return
        if self.image.isNull() or self.image.width() != size or self.image.height() != size:
            img = QImage(int(size), int(size), QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(QColor(0, 0, 0, 0))
            self.image = img

    def render(self, painter: QPainter) -> None:
        if self.image.isNull():
            return
        painter.drawImage(self.position, self.image)


@dataclass
class ImageLayer(Layer):
    source: QImage = field(default_factory=lambda: QImage())
    scale: float = 1.0
    rotation_deg: float = 0.0  # optional; UI may ignore
    crop_rect: Optional[QRectF] = None  # in source-image coordinates

    def render(self, painter: QPainter) -> None:
        if self.source.isNull():
            return

        img = self.source
        if self.crop_rect is not None:
            try:
                r = self.crop_rect
                x0 = int(max(0.0, min(float(img.width()), float(r.x()))))
                y0 = int(max(0.0, min(float(img.height()), float(r.y()))))
                x1 = int(max(0.0, min(float(img.width()), float(r.x() + r.width()))))
                y1 = int(max(0.0, min(float(img.height()), float(r.y() + r.height()))))
                w = max(1, x1 - x0)
                h = max(1, y1 - y0)
                img = img.copy(x0, y0, w, h)
            except Exception:
                img = self.source

        painter.save()
        painter.translate(self.position)
        if self.rotation_deg:
            painter.rotate(float(self.rotation_deg))
        painter.scale(float(self.scale), float(self.scale))
        painter.drawImage(QPointF(0.0, 0.0), img)
        painter.restore()


@dataclass
class TextLayer(Layer):
    text: str = ""
    font_family: str = ""
    font_point_size: int = 28
    color: QColor = field(default_factory=lambda: QColor(255, 255, 255))

    def render(self, painter: QPainter) -> None:
        if not self.text:
            return
        painter.save()
        f = QFont()
        if self.font_family:
            try:
                f.setFamily(str(self.font_family))
            except Exception:
                pass
        try:
            f.setPointSize(int(self.font_point_size))
        except Exception:
            pass
        painter.setFont(f)
        painter.setPen(self.color)
        # QGraphicsTextItem positions text by its top-left corner, but QPainter.drawText(QPointF, str)
        # interprets the point as the text baseline. Offset by ascent so exported PNG matches editor.
        try:
            fm = QFontMetricsF(f)
            y = float(self.position.y()) + float(fm.ascent())
            painter.drawText(QPointF(float(self.position.x()), y), self.text)
        except Exception:
            painter.drawText(self.position, self.text)
        painter.restore()


@dataclass
class CanvasDocument:
    size: int = 256  # square
    layers: list[Layer] = field(default_factory=list)

    def set_size(self, size: int) -> None:
        size = int(size)
        if size <= 0:
            return
        self.size = size
        for layer in self.layers:
            if isinstance(layer, RasterLayer):
                layer.ensure_size(size)

    def sorted_layers(self) -> Sequence[Layer]:
        return sorted(self.layers, key=lambda l: int(getattr(l, "z_index", 0)))

    def export_composite(self) -> QImage:
        img = QImage(int(self.size), int(self.size), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        try:
            for layer in self.sorted_layers():
                if not getattr(layer, "visible", True):
                    continue
                op = float(getattr(layer, "opacity", 1.0) or 0.0)
                op = max(0.0, min(1.0, op))
                p.setOpacity(op)
                layer.render(p)
        finally:
            p.end()
        return img
