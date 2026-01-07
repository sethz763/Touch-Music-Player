from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap,
                           QTransform, QUndoCommand, QUndoStack)
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem,
                               QGraphicsView, QWidget)

from ui.models.layers import CanvasDocument, ImageLayer, Layer, RasterLayer, TextLayer


class _FnCommand(QUndoCommand):
    def __init__(self, text: str, do_fn, undo_fn) -> None:
        super().__init__(text)
        self._do_fn = do_fn
        self._undo_fn = undo_fn

    def redo(self) -> None:
        self._do_fn()

    def undo(self) -> None:
        self._undo_fn()


class _ResizeHandle(QGraphicsRectItem):
    def __init__(self, editor: "LayeredCanvasEditor", corner: str) -> None:
        # Slightly larger than 10x10 so it's easier to grab.
        super().__init__(-7.0, -7.0, 14.0, 14.0)
        self._editor = editor
        self._corner = str(corner)
        self.setZValue(9500)
        self.setBrush(QBrush(QColor(255, 255, 255, 220)))
        self.setPen(QPen(QColor(0, 0, 0, 180)))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        try:
            self.setAcceptHoverEvents(True)
            # Cursor is set on hover in hoverMoveEvent for compatibility.
        except Exception:
            pass
        try:
            self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        except Exception:
            pass

    def mousePressEvent(self, event) -> None:
        try:
            self._editor._begin_handle_resize(self._corner, event.scenePos())
        except Exception:
            pass
        # QGraphicsRectItem's default handlers ignore events when not selectable/movable,
        # which prevents us from receiving drag updates. Explicitly accept.
        try:
            event.accept()
        except Exception:
            pass

    def mouseMoveEvent(self, event) -> None:
        try:
            self._editor._update_handle_resize(event.scenePos())
        except Exception:
            pass
        try:
            event.accept()
        except Exception:
            pass

    def mouseReleaseEvent(self, event) -> None:
        try:
            self._editor._end_handle_resize(event.scenePos())
        except Exception:
            pass
        try:
            event.accept()
        except Exception:
            pass

    def hoverMoveEvent(self, event) -> None:
        # Show a resize cursor on hover.
        try:
            if self._corner in ("tl", "br"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        except Exception:
            pass
        try:
            event.accept()
        except Exception:
            pass


class LayeredCanvasEditor(QWidget):
    """Minimal layered canvas editor (square)."""

    changed = Signal()
    layers_changed = Signal()
    active_layer_changed = Signal(object)

    class Tool:
        SELECT = "select"
        MOVE = "move"
        BRUSH = "brush"
        ERASER = "eraser"
        RECT = "rect"
        ELLIPSE = "ellipse"
        TEXT = "text"
        EMOJI = "emoji"
        CROP = "crop"

    def __init__(self, parent: Optional[QWidget] = None, *, canvas_size: int = 256) -> None:
        super().__init__(parent)

        self.doc = CanvasDocument(size=int(canvas_size))

        self._scene = QGraphicsScene(self)
        self._view = _CanvasView(self._scene, self)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            self._view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
            self._view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        except Exception:
            pass

        # Ensure we refit when the *viewport* size changes (QSplitter/layout can resize the view
        # after the editor widget's own resize event).
        try:
            vp = self._view.viewport()
            if vp is not None:
                vp.installEventFilter(self)
        except Exception:
            pass

        self._view.canvas_mouse_press.connect(self._on_canvas_mouse_press)
        self._view.canvas_mouse_move.connect(self._on_canvas_mouse_move)
        self._view.canvas_mouse_release.connect(self._on_canvas_mouse_release)
        self._view.canvas_wheel.connect(self._on_canvas_wheel)
        self._view.canvas_drop_image.connect(self._on_canvas_drop_image)

        self._undo = QUndoStack(self)
        self._undo.setUndoLimit(30)
        self._applying_undo = False

        self._tool: str = self.Tool.SELECT
        self._brush_color = QColor(255, 255, 255)
        self._brush_size = 10
        self._pending_text: Optional[str] = None
        self._pending_emoji: Optional[str] = None

        self._active_layer: Optional[Layer] = None

        self._paint_last: Optional[QPointF] = None
        self._shape_start: Optional[QPointF] = None
        self._shape_preview: Optional[QGraphicsItem] = None
        self._crop_start: Optional[QPointF] = None
        self._crop_preview: Optional[QGraphicsRectItem] = None

        # Visual guide for canvas bounds (helps see exported edge).
        self._canvas_guide: Optional[QGraphicsRectItem] = None

        # Extra scene margin so the canvas border is visible and the canvas reads as centered.
        self._scene_margin: float = 12.0

        # Map by object id (layers are mutable dataclasses and intentionally unhashable).
        self._layer_to_item: dict[int, QGraphicsItem] = {}

        # Move tracking for undo.
        self._move_tracking_layer: Optional[Layer] = None
        self._move_start_pos: Optional[QPointF] = None

        # Raster stroke tracking for undo.
        self._raster_before: Optional[QImage] = None
        self._raster_before_layer: Optional[RasterLayer] = None

        # Resize-handle overlay.
        self._resize_outline: Optional[QGraphicsRectItem] = None
        self._resize_handles: list[_ResizeHandle] = []
        self._resize_target_layer: Optional[Layer] = None
        self._resize_active: bool = False
        self._resize_center: Optional[QPointF] = None
        self._resize_start_dist: float = 1.0
        self._resize_start_image_scale: float = 1.0
        self._resize_start_text_pt: int = 28

        # Keep resize overlay tracking during interactive moves.
        self._overlay_update_scheduled: bool = False
        self._overlay_update_in_progress: bool = False

        self._scene.selectionChanged.connect(self._on_scene_selection_changed)

        # Canvas bounds guide (drawn above content).
        try:
            guide = QGraphicsRectItem(self._canvas_guide_rect())
            pen = QPen(QColor(255, 255, 255, 200))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidth(2)
            guide.setPen(pen)
            guide.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            guide.setZValue(9000)
            guide.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            guide.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            try:
                guide.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            except Exception:
                pass
            self._scene.addItem(guide)
            self._canvas_guide = guide
        except Exception:
            self._canvas_guide = None

        # A default raster layer so painting works immediately.
        self.add_raster_layer("Background")
        self.set_active_layer(self.doc.layers[0])
        self.set_canvas_size(int(canvas_size))

        # Ensure the initial fit happens after layout/geometry is established.
        QTimer.singleShot(0, self._fit_view)

        # Basic layout: fill widget with the view.
        self._view.setParent(self)

    def _schedule_overlay_refresh(self) -> None:
        # Movement of selected QGraphicsItems doesn't emit selectionChanged, so keep
        # the overlay tracking by updating it after the view processes mouse moves.
        if self._overlay_update_in_progress or self._overlay_update_scheduled:
            return
        if self._resize_active:
            return
        if self._resize_target_layer is None or self._resize_outline is None or not self._resize_handles:
            return
        self._overlay_update_scheduled = True

        def _do() -> None:
            self._overlay_update_scheduled = False
            if self._resize_target_layer is None or self._resize_outline is None or not self._resize_handles:
                return
            self._overlay_update_in_progress = True
            try:
                self._update_resize_overlay_geometry()
            except Exception:
                # If the underlying C++ items were deleted (e.g., designer closing), ignore.
                pass
            finally:
                self._overlay_update_in_progress = False

        QTimer.singleShot(0, _do)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._view.setGeometry(self.rect())
        self._fit_view()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # On first show, the viewport size becomes valid; refit so the guide/export area is correct.
        QTimer.singleShot(0, self._fit_view)

    def eventFilter(self, obj, event) -> bool:
        try:
            if obj is self._view.viewport() and event is not None:
                et = event.type()
                if et in (QEvent.Type.Resize, QEvent.Type.Show):
                    # Debounce via next tick so Qt has applied scroll/geometry.
                    QTimer.singleShot(0, self._fit_view)
        except Exception:
            pass
        return super().eventFilter(obj, event)

    # ---------------------- public API ----------------------

    def set_undo_limit(self, levels: int) -> None:
        try:
            self._undo.setUndoLimit(max(0, int(levels)))
        except Exception:
            pass

    def undo(self) -> None:
        try:
            self._applying_undo = True
            self._undo.undo()
        finally:
            self._applying_undo = False
        self._sync_model_from_items()
        self._on_scene_selection_changed()
        self.changed.emit()

    def redo(self) -> None:
        try:
            self._applying_undo = True
            self._undo.redo()
        finally:
            self._applying_undo = False
        self._sync_model_from_items()
        self._on_scene_selection_changed()
        self.changed.emit()

    def set_tool(self, tool: str) -> None:
        self._tool = str(tool)
        # Disable default scene item interaction while painting/shaping so movable
        # layers (and resize handles) can't steal the drag.
        try:
            allow = self._tool in (self.Tool.SELECT, self.Tool.MOVE)
            self._view.setInteractive(bool(allow))
        except Exception:
            pass

    def set_brush_color(self, color: QColor) -> None:
        if isinstance(color, QColor):
            self._brush_color = color

    def set_brush_size(self, size: int) -> None:
        try:
            self._brush_size = max(1, int(size))
        except Exception:
            pass

    def set_pending_text(self, text: Optional[str]) -> None:
        self._pending_text = text

    def set_pending_emoji(self, emoji: Optional[str]) -> None:
        self._pending_emoji = emoji

    def set_canvas_size(self, size: int) -> None:
        self.doc.set_size(int(size))
        # Add a small margin so the border isn't clipped and the canvas reads as centered.
        m = float(getattr(self, "_scene_margin", 0.0) or 0.0)
        self._scene.setSceneRect(-m, -m, float(self.doc.size) + 2.0 * m, float(self.doc.size) + 2.0 * m)

        # Update canvas guide rect.
        try:
            if self._canvas_guide is not None:
                self._canvas_guide.setRect(self._canvas_guide_rect())
        except Exception:
            pass

        # Ensure raster layers match size.
        for layer in self.doc.layers:
            if isinstance(layer, RasterLayer):
                layer.ensure_size(self.doc.size)
                item = self._layer_to_item.get(id(layer))
                if isinstance(item, QGraphicsPixmapItem):
                    item.setPixmap(QPixmap.fromImage(layer.image))
        self._fit_view()
        self.changed.emit()

    def export_composite(self) -> QImage:
        # Render via model for correctness.
        # Keep z-index consistent with scene ordering.
        return self.doc.export_composite()

    def add_raster_layer(self, name: str = "Raster") -> RasterLayer:
        layer = RasterLayer(name=str(name), z_index=len(self.doc.layers))
        layer.ensure_size(self.doc.size)
        self.doc.layers.append(layer)

        pix = QGraphicsPixmapItem(QPixmap.fromImage(layer.image))
        pix.setZValue(float(layer.z_index))
        pix.setPos(QPointF(0.0, 0.0))
        pix.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        pix.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._scene.addItem(pix)
        lid = id(layer)
        try:
            pix.setData(0, int(lid))
        except Exception:
            pass
        self._layer_to_item[lid] = pix

        self.layers_changed.emit()
        self.changed.emit()
        return layer

    def _add_existing_layer(self, layer: Layer) -> None:
        if layer in self.doc.layers:
            return
        layer.z_index = len(self.doc.layers)
        self.doc.layers.append(layer)
        lid = id(layer)

        if isinstance(layer, RasterLayer):
            layer.ensure_size(self.doc.size)
            item = QGraphicsPixmapItem(QPixmap.fromImage(layer.image))
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        elif isinstance(layer, ImageLayer):
            img = layer.source
            try:
                if layer.crop_rect is not None and not img.isNull():
                    r = layer.crop_rect
                    img = img.copy(int(r.x()), int(r.y()), int(r.width()), int(r.height()))
            except Exception:
                img = layer.source
            item = QGraphicsPixmapItem(QPixmap.fromImage(img))
            try:
                item.setTransform(QTransform().scale(float(layer.scale), float(layer.scale)))
            except Exception:
                pass
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        elif isinstance(layer, TextLayer):
            item = QGraphicsTextItem(layer.text)
            f = QFont()
            if layer.font_family:
                try:
                    f.setFamily(layer.font_family)
                except Exception:
                    pass
            try:
                f.setPointSize(int(layer.font_point_size))
            except Exception:
                pass
            item.setFont(f)
            item.setDefaultTextColor(layer.color)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        else:
            return

        item.setZValue(float(layer.z_index))
        try:
            item.setPos(QPointF(layer.position))
        except Exception:
            item.setPos(QPointF(0.0, 0.0))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._scene.addItem(item)
        try:
            item.setData(0, int(lid))
        except Exception:
            pass
        self._layer_to_item[lid] = item
        self._reassign_z()
        self.layers_changed.emit()
        self.changed.emit()

    def add_image_layer_from_path(self, path: str, name: str = "Dropped Image") -> Optional[ImageLayer]:
        if not path:
            return None
        img = QImage(str(path))
        if img.isNull():
            return None

        layer = ImageLayer(name=str(name), z_index=len(self.doc.layers), source=img)
        # Scale to fit inside canvas.
        try:
            scale = min(
                float(self.doc.size) / float(max(1, img.width())),
                float(self.doc.size) / float(max(1, img.height())),
            )
            layer.scale = float(scale)
        except Exception:
            layer.scale = 1.0

        # Center by default.
        try:
            dx = (float(self.doc.size) - float(img.width()) * layer.scale) * 0.5
            dy = (float(self.doc.size) - float(img.height()) * layer.scale) * 0.5
            layer.position = QPointF(dx, dy)
        except Exception:
            layer.position = QPointF(0.0, 0.0)

        self.doc.layers.append(layer)

        pix = QGraphicsPixmapItem(QPixmap.fromImage(img))
        pix.setZValue(float(layer.z_index))
        pix.setPos(layer.position)
        pix.setTransform(QTransform().scale(layer.scale, layer.scale))
        pix.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        pix.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self._scene.addItem(pix)
        lid = id(layer)
        try:
            pix.setData(0, int(lid))
        except Exception:
            pass
        self._layer_to_item[lid] = pix

        self.layers_changed.emit()
        self.changed.emit()
        return layer

    def add_text_layer(self, text: str, *, name: str = "Text", font_family: str = "", font_size: int = 28, color: Optional[QColor] = None) -> TextLayer:
        layer = TextLayer(
            name=str(name),
            z_index=len(self.doc.layers),
            text=str(text or ""),
            font_family=str(font_family or ""),
            font_point_size=int(font_size),
            color=color if isinstance(color, QColor) else QColor(255, 255, 255),
        )
        layer.position = QPointF(20.0, 60.0)
        self.doc.layers.append(layer)

        item = QGraphicsTextItem(layer.text)
        f = QFont()
        if layer.font_family:
            f.setFamily(layer.font_family)
        f.setPointSize(layer.font_point_size)
        item.setFont(f)
        item.setDefaultTextColor(layer.color)
        item.setZValue(float(layer.z_index))
        item.setPos(layer.position)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self._scene.addItem(item)
        lid = id(layer)
        try:
            item.setData(0, int(lid))
        except Exception:
            pass
        self._layer_to_item[lid] = item

        self.layers_changed.emit()
        self.changed.emit()
        return layer

    def remove_layer(self, layer: Layer) -> None:
        if layer not in self.doc.layers:
            return
        item = self._layer_to_item.pop(id(layer), None)
        if item is not None:
            try:
                self._scene.removeItem(item)
            except Exception:
                pass
        try:
            self.doc.layers.remove(layer)
        except Exception:
            pass
        if self._active_layer is layer:
            self._active_layer = None
        self._reassign_z()
        self.layers_changed.emit()
        self.changed.emit()

    def _remove_layer_keep_object(self, layer: Layer) -> None:
        if layer not in self.doc.layers:
            return
        item = self._layer_to_item.pop(id(layer), None)
        if item is not None:
            try:
                self._scene.removeItem(item)
            except Exception:
                pass
        try:
            self.doc.layers.remove(layer)
        except Exception:
            pass
        if self._active_layer is layer:
            self._active_layer = None
        self._reassign_z()
        self.layers_changed.emit()
        self.changed.emit()

    def reorder_layer(self, layer: Layer, delta: int) -> None:
        if layer not in self.doc.layers:
            return
        i = self.doc.layers.index(layer)
        j = max(0, min(len(self.doc.layers) - 1, i + int(delta)))
        if i == j:
            return
        self.doc.layers.insert(j, self.doc.layers.pop(i))
        self._reassign_z()
        self.layers_changed.emit()
        self.changed.emit()

    def rename_layer(self, layer: Layer, name: str) -> None:
        if layer in self.doc.layers:
            layer.name = str(name)
            self.layers_changed.emit()
            self.changed.emit()

    def set_layer_visible(self, layer: Layer, visible: bool) -> None:
        if layer not in self.doc.layers:
            return
        layer.visible = bool(visible)
        item = self._layer_to_item.get(id(layer))
        if item is not None:
            item.setVisible(bool(visible))
        self.changed.emit()

    def set_active_layer(self, layer: Optional[Layer]) -> None:
        self._active_layer = layer
        self.active_layer_changed.emit(layer)
        try:
            if layer is not None:
                item = self._layer_to_item.get(id(layer))
                if item is not None:
                    self._scene.clearSelection()
                    item.setSelected(True)
        except Exception:
            pass

    def active_layer(self) -> Optional[Layer]:
        return self._active_layer

    # ---------------------- internal helpers ----------------------

    def _fit_view(self) -> None:
        try:
            vp = self._view.viewport()
            if vp is None or vp.width() < 2 or vp.height() < 2:
                return
            r = self._scene.sceneRect()
            self._view.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
            # Some Qt styles keep the internal scroll offset at (0,0) after fitInView.
            # Explicitly center to ensure the export/guide rect is centered vertically.
            self._view.centerOn(r.center())
        except Exception:
            pass

    def _reassign_z(self) -> None:
        for i, layer in enumerate(self.doc.layers):
            layer.z_index = i
            item = self._layer_to_item.get(id(layer))
            if item is not None:
                item.setZValue(float(i))

    def _layer_from_item(self, it: QGraphicsItem) -> Optional[Layer]:
        """Resolve a model Layer from a QGraphicsItem (best-effort)."""
        lid = None
        try:
            lid = it.data(0)
        except Exception:
            lid = None

        if lid is not None:
            try:
                lid_int = int(lid)
                for layer in self.doc.layers:
                    if id(layer) == lid_int:
                        return layer
            except Exception:
                pass

        # Fallback: linear search by identity through mapping.
        try:
            for layer in self.doc.layers:
                item = self._layer_to_item.get(id(layer))
                if item is it:
                    return layer
        except Exception:
            pass
        return None

    def _canvas_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, float(self.doc.size), float(self.doc.size))

    def _canvas_guide_rect(self) -> QRectF:
        # Keep the stroke fully inside the canvas bounds so it doesn't get clipped.
        # (Pen width is 2px; inset by 1px.)
        try:
            inset = 1.0
            s = float(self.doc.size)
            return QRectF(inset, inset, max(0.0, s - 2.0 * inset), max(0.0, s - 2.0 * inset))
        except Exception:
            return QRectF(0.0, 0.0, float(self.doc.size), float(self.doc.size))

    def _clamp_to_canvas(self, p: QPointF) -> QPointF:
        try:
            x = float(p.x())
            y = float(p.y())
        except Exception:
            return QPointF(0.0, 0.0)
        s = float(self.doc.size)
        x = max(0.0, min(s, x))
        y = max(0.0, min(s, y))
        return QPointF(x, y)

    def _selected_layer_from_scene(self) -> Optional[Layer]:
        try:
            items = self._scene.selectedItems()
        except Exception:
            items = []
        if not items:
            return None
        it = items[0]
        return self._layer_from_item(it)

    def _sync_model_from_items(self) -> None:
        for layer in list(self.doc.layers):
            item = self._layer_to_item.get(id(layer))
            if item is None:
                continue
            try:
                layer.position = QPointF(item.pos())
            except Exception:
                pass
            if isinstance(layer, ImageLayer) and isinstance(item, QGraphicsPixmapItem):
                try:
                    t = item.transform()
                    layer.scale = float(t.m11())
                except Exception:
                    pass

    # ---------------------- mouse handling ----------------------

    def _on_canvas_mouse_press(self, scene_pos: QPointF, button: Qt.MouseButton) -> None:
        if button != Qt.MouseButton.LeftButton:
            return

        if self._tool in (self.Tool.SELECT, self.Tool.MOVE):
            items = self._scene.items(scene_pos)
            if items:
                top = items[0]
                # Ignore resize handles/outline.
                try:
                    if isinstance(top, _ResizeHandle) or top is self._resize_outline:
                        return
                except Exception:
                    pass
                try:
                    top.setSelected(True)
                except Exception:
                    pass
                layer = self._layer_from_item(top)
                self.set_active_layer(layer)
                # Track position for undo (only for movable layers).
                if layer is not None:
                    self._move_tracking_layer = layer
                    try:
                        self._move_start_pos = QPointF(top.pos())
                    except Exception:
                        self._move_start_pos = QPointF(layer.position)
            return

        if self._tool in (self.Tool.BRUSH, self.Tool.ERASER):
            # Snapshot raster for undo.
            try:
                rl = self._active_raster_layer()
                if rl is not None:
                    self._raster_before_layer = rl
                    self._raster_before = QImage(rl.image)
            except Exception:
                self._raster_before_layer = None
                self._raster_before = None
            self._paint_last = scene_pos
            self._paint_at(scene_pos, scene_pos)
            return

        if self._tool in (self.Tool.RECT, self.Tool.ELLIPSE):
            try:
                rl = self._active_raster_layer()
                if rl is not None:
                    self._raster_before_layer = rl
                    self._raster_before = QImage(rl.image)
            except Exception:
                self._raster_before_layer = None
                self._raster_before = None
            self._shape_start = scene_pos
            self._shape_preview = self._make_shape_preview(scene_pos, scene_pos)
            return

        if self._tool == self.Tool.TEXT:
            txt = (self._pending_text or "Text").strip() or "Text"
            layer = self.add_text_layer(txt, name="Text")
            it = self._layer_to_item.get(id(layer))
            if it is not None:
                it.setPos(scene_pos)
            self._sync_model_from_items()
            self.set_active_layer(layer)
            if not self._applying_undo:
                self._undo.push(
                    _FnCommand(
                        "Add Text",
                        lambda l=layer: self._add_existing_layer(l),
                        lambda l=layer: self._remove_layer_keep_object(l),
                    )
                )
            return

        if self._tool == self.Tool.EMOJI:
            em = (self._pending_emoji or "🙂").strip() or "🙂"
            # Prefer Windows emoji font.
            layer = self.add_text_layer(em, name="Emoji", font_family="Segoe UI Emoji", font_size=64)
            it = self._layer_to_item.get(id(layer))
            if it is not None:
                it.setPos(scene_pos)
            self._sync_model_from_items()
            self.set_active_layer(layer)
            if not self._applying_undo:
                self._undo.push(
                    _FnCommand(
                        "Add Emoji",
                        lambda l=layer: self._add_existing_layer(l),
                        lambda l=layer: self._remove_layer_keep_object(l),
                    )
                )
            return

        if self._tool == self.Tool.CROP:
            self._crop_start = scene_pos
            self._crop_preview = QGraphicsRectItem(QRectF(scene_pos, scene_pos))
            pen = QPen(QColor(255, 255, 255, 220))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidth(2)
            self._crop_preview.setPen(pen)
            self._crop_preview.setBrush(QBrush(QColor(0, 0, 0, 30)))
            self._crop_preview.setZValue(10_000)
            self._scene.addItem(self._crop_preview)
            return

    def _on_canvas_mouse_move(self, scene_pos: QPointF, buttons: Qt.MouseButtons) -> None:
        if not (buttons & Qt.MouseButton.LeftButton):
            return

        # If the user is interactively moving a selected item (Qt's default behavior),
        # schedule an overlay refresh after the view processes the move.
        if self._tool in (self.Tool.SELECT, self.Tool.MOVE):
            self._schedule_overlay_refresh()

        if self._tool in (self.Tool.BRUSH, self.Tool.ERASER):
            if self._paint_last is None:
                self._paint_last = scene_pos
            self._paint_at(self._paint_last, scene_pos)
            self._paint_last = scene_pos
            return

        if self._tool in (self.Tool.RECT, self.Tool.ELLIPSE):
            if self._shape_preview is not None and self._shape_start is not None:
                self._update_shape_preview(self._shape_start, scene_pos)
            return

        if self._tool == self.Tool.CROP:
            if self._crop_preview is not None and self._crop_start is not None:
                r = QRectF(self._crop_start, scene_pos).normalized()
                self._crop_preview.setRect(r)
            return

    def _on_canvas_mouse_release(self, scene_pos: QPointF, button: Qt.MouseButton) -> None:
        if button != Qt.MouseButton.LeftButton:
            return

        if self._tool in (self.Tool.BRUSH, self.Tool.ERASER):
            self._paint_last = None
            # Push raster undo snapshot.
            if not self._applying_undo and self._raster_before_layer is not None and self._raster_before is not None:
                layer = self._raster_before_layer
                before = QImage(self._raster_before)
                after = QImage(layer.image)

                def _apply(img: QImage) -> None:
                    layer.image = QImage(img)
                    it = self._layer_to_item.get(id(layer))
                    if isinstance(it, QGraphicsPixmapItem):
                        it.setPixmap(QPixmap.fromImage(layer.image))

                self._undo.push(_FnCommand("Brush Stroke", lambda: _apply(after), lambda: _apply(before)))
            self._raster_before_layer = None
            self._raster_before = None
            return

        if self._tool in (self.Tool.RECT, self.Tool.ELLIPSE):
            if self._shape_start is None:
                return
            self._commit_shape(self._shape_start, scene_pos)
            self._shape_start = None
            if self._shape_preview is not None:
                try:
                    self._scene.removeItem(self._shape_preview)
                except Exception:
                    pass
                self._shape_preview = None
            # Push raster undo snapshot.
            if not self._applying_undo and self._raster_before_layer is not None and self._raster_before is not None:
                layer = self._raster_before_layer
                before = QImage(self._raster_before)
                after = QImage(layer.image)

                def _apply(img: QImage) -> None:
                    layer.image = QImage(img)
                    it = self._layer_to_item.get(id(layer))
                    if isinstance(it, QGraphicsPixmapItem):
                        it.setPixmap(QPixmap.fromImage(layer.image))

                self._undo.push(_FnCommand("Draw Shape", lambda: _apply(after), lambda: _apply(before)))
            self._raster_before_layer = None
            self._raster_before = None
            return

        if self._tool == self.Tool.CROP:
            self._commit_crop()
            return

        # For moving items, update model.
        if self._tool in (self.Tool.SELECT, self.Tool.MOVE):
            layer = self._move_tracking_layer
            start_pos = self._move_start_pos
            self._sync_model_from_items()
            if (
                not self._applying_undo
                and layer is not None
                and start_pos is not None
                and isinstance(layer, (ImageLayer, TextLayer))
            ):
                try:
                    end_pos = QPointF(layer.position)
                except Exception:
                    end_pos = start_pos
                dx = float(end_pos.x() - start_pos.x())
                dy = float(end_pos.y() - start_pos.y())
                if abs(dx) > 0.25 or abs(dy) > 0.25:
                    it = self._layer_to_item.get(id(layer))

                    def _apply(p: QPointF) -> None:
                        try:
                            layer.position = QPointF(p)
                        except Exception:
                            pass
                        if it is not None:
                            try:
                                it.setPos(QPointF(p))
                            except Exception:
                                pass

                    self._undo.push(_FnCommand("Move Layer", lambda: _apply(end_pos), lambda: _apply(start_pos)))

            self._move_tracking_layer = None
            self._move_start_pos = None
            self.changed.emit()

    def _on_canvas_wheel(self, scene_pos: QPointF, delta_y: int, modifiers: Qt.KeyboardModifiers) -> None:
        # Only allow scaling interactions when explicitly in Select/Move.
        # Otherwise Qt's default item interaction (and our scaling) can interfere with painting.
        if self._tool not in (self.Tool.SELECT, self.Tool.MOVE):
            return
        # Ctrl+wheel: scale selected layer (Image: transform scale, Text/Emoji: font size)
        if not (modifiers & Qt.KeyboardModifier.ControlModifier):
            return
        layer = self._selected_layer_from_scene()
        item = self._layer_to_item.get(id(layer))
        factor = 1.05 if delta_y > 0 else 0.95

        if isinstance(layer, ImageLayer):
            if not isinstance(item, QGraphicsPixmapItem):
                return
            try:
                t = item.transform()
                cur = float(t.m11())
            except Exception:
                cur = float(getattr(layer, "scale", 1.0) or 1.0)

            # Allow scaling well beyond canvas size for large source images.
            new_s = max(0.01, min(256.0, cur * factor))
            item.setTransform(QTransform().scale(new_s, new_s))
            self._sync_model_from_items()
            self.changed.emit()
            return

        if isinstance(layer, TextLayer):
            if not isinstance(item, QGraphicsTextItem):
                return
            try:
                cur_pt = int(getattr(layer, "font_point_size", 28) or 28)
            except Exception:
                cur_pt = 28

            # Scale font size with a multiplicative factor.
            new_pt = int(round(float(cur_pt) * (1.0 / factor if delta_y < 0 else factor)))
            new_pt = max(6, min(256, new_pt))

            try:
                layer.font_point_size = int(new_pt)
            except Exception:
                pass
            try:
                f = item.font()
                f.setPointSize(int(new_pt))
                item.setFont(f)
            except Exception:
                pass

            self.changed.emit()
            return

    def _on_canvas_drop_image(self, path: str) -> None:
        layer = self.add_image_layer_from_path(path, name="Dropped Image")
        if layer is not None:
            self.set_active_layer(layer)
            if not self._applying_undo:
                self._undo.push(
                    _FnCommand(
                        "Add Image",
                        lambda l=layer: self._add_existing_layer(l),
                        lambda l=layer: self._remove_layer_keep_object(l),
                    )
                )

    # ---------------------- painting ----------------------

    def _active_raster_layer(self) -> Optional[RasterLayer]:
        layer = self._active_layer
        if isinstance(layer, RasterLayer):
            return layer
        # Fall back to first raster.
        for l in self.doc.layers:
            if isinstance(l, RasterLayer):
                return l
        return None

    def _paint_at(self, a: QPointF, b: QPointF) -> None:
        layer = self._active_raster_layer()
        if layer is None:
            return
        item = self._layer_to_item.get(id(layer))
        if not isinstance(item, QGraphicsPixmapItem):
            return

        img = layer.image
        if img.isNull():
            return

        a = self._clamp_to_canvas(a)
        b = self._clamp_to_canvas(b)

        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._tool == self.Tool.ERASER:
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                pen = QPen(QColor(0, 0, 0, 0))
            else:
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                pen = QPen(self._brush_color)
            pen.setWidth(int(self._brush_size))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawLine(a, b)
        finally:
            p.end()

        item.setPixmap(QPixmap.fromImage(img))
        self.changed.emit()

    # ---------------------- resize handles ----------------------

    def _on_scene_selection_changed(self) -> None:
        # Show handles only for non-raster layers.
        layer = self._selected_layer_from_scene()
        if layer is None or isinstance(layer, RasterLayer):
            self._clear_resize_overlay()
            return
        self._ensure_resize_overlay(layer)
        self._update_resize_overlay_geometry()

    def _clear_resize_overlay(self) -> None:
        try:
            if self._resize_outline is not None:
                self._scene.removeItem(self._resize_outline)
        except Exception:
            pass
        self._resize_outline = None
        for h in list(self._resize_handles):
            try:
                self._scene.removeItem(h)
            except Exception:
                pass
        self._resize_handles = []
        self._resize_target_layer = None

    def _ensure_resize_overlay(self, layer: Layer) -> None:
        self._resize_target_layer = layer
        if self._resize_outline is None:
            o = QGraphicsRectItem(QRectF(0.0, 0.0, 10.0, 10.0))
            o.setZValue(9400)
            pen = QPen(QColor(255, 255, 255, 180))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidth(1)
            o.setPen(pen)
            o.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            o.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            o.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            try:
                o.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            except Exception:
                pass
            self._scene.addItem(o)
            self._resize_outline = o

        if not self._resize_handles:
            self._resize_handles = [
                _ResizeHandle(self, "tl"),
                _ResizeHandle(self, "tr"),
                _ResizeHandle(self, "bl"),
                _ResizeHandle(self, "br"),
            ]
            for h in self._resize_handles:
                self._scene.addItem(h)

    def _update_resize_overlay_geometry(self) -> None:
        layer = self._resize_target_layer
        if layer is None:
            return
        it = self._layer_to_item.get(id(layer))
        if it is None:
            return
        try:
            r = it.sceneBoundingRect()
        except Exception:
            return
        # Position outline/handles in scene coords to match the selected item's scene bounding rect.
        if self._resize_outline is not None:
            try:
                self._resize_outline.setRect(0.0, 0.0, float(r.width()), float(r.height()))
                self._resize_outline.setPos(QPointF(r.topLeft()))
            except Exception:
                pass

        corners = {
            "tl": r.topLeft(),
            "tr": r.topRight(),
            "bl": r.bottomLeft(),
            "br": r.bottomRight(),
        }
        for h in self._resize_handles:
            try:
                h.setPos(QPointF(corners.get(getattr(h, "_corner", "tl"), r.topLeft())))
            except Exception:
                pass

    def _begin_handle_resize(self, corner: str, scene_pos: QPointF) -> None:
        layer = self._resize_target_layer
        if layer is None:
            return
        it = self._layer_to_item.get(id(layer))
        if it is None:
            return

        try:
            r = it.sceneBoundingRect()
            self._resize_center = QPointF(r.center())
        except Exception:
            self._resize_center = QPointF(0.0, 0.0)

        c = self._resize_center
        dx = float(scene_pos.x() - c.x())
        dy = float(scene_pos.y() - c.y())
        self._resize_start_dist = max(1e-3, math.hypot(dx, dy))

        if isinstance(layer, ImageLayer):
            try:
                t = it.transform()
                self._resize_start_image_scale = float(t.m11())
            except Exception:
                self._resize_start_image_scale = float(getattr(layer, "scale", 1.0) or 1.0)

        if isinstance(layer, TextLayer):
            try:
                self._resize_start_text_pt = int(getattr(layer, "font_point_size", 28) or 28)
            except Exception:
                self._resize_start_text_pt = 28

        self._resize_active = True

    def _update_handle_resize(self, scene_pos: QPointF) -> None:
        if not self._resize_active:
            return
        layer = self._resize_target_layer
        if layer is None:
            return
        it = self._layer_to_item.get(id(layer))
        if it is None:
            return
        c = self._resize_center
        if c is None:
            return
        dx = float(scene_pos.x() - c.x())
        dy = float(scene_pos.y() - c.y())
        dist = max(1e-3, math.hypot(dx, dy))
        factor = dist / max(1e-3, float(self._resize_start_dist))

        if isinstance(layer, ImageLayer) and isinstance(it, QGraphicsPixmapItem):
            new_s = max(0.01, min(256.0, float(self._resize_start_image_scale) * float(factor)))
            try:
                it.setTransform(QTransform().scale(new_s, new_s))
            except Exception:
                return
            self._sync_model_from_items()
            self._update_resize_overlay_geometry()
            self.changed.emit()
            return

        if isinstance(layer, TextLayer) and isinstance(it, QGraphicsTextItem):
            new_pt = int(round(float(self._resize_start_text_pt) * float(factor)))
            new_pt = max(6, min(256, new_pt))
            try:
                layer.font_point_size = int(new_pt)
            except Exception:
                pass
            try:
                f = it.font()
                f.setPointSize(int(new_pt))
                it.setFont(f)
            except Exception:
                pass
            self._sync_model_from_items()
            self._update_resize_overlay_geometry()
            self.changed.emit()
            return

    def _end_handle_resize(self, scene_pos: QPointF) -> None:
        if not self._resize_active:
            return
        self._resize_active = False
        layer = self._resize_target_layer
        if layer is None or self._applying_undo:
            return
        it = self._layer_to_item.get(id(layer))
        if it is None:
            return

        if isinstance(layer, ImageLayer) and isinstance(it, QGraphicsPixmapItem):
            before = float(self._resize_start_image_scale)
            try:
                after = float(it.transform().m11())
            except Exception:
                after = float(getattr(layer, "scale", 1.0) or 1.0)
            if abs(after - before) > 1e-4:

                def _apply(s: float) -> None:
                    try:
                        it.setTransform(QTransform().scale(float(s), float(s)))
                    except Exception:
                        pass
                    try:
                        layer.scale = float(s)
                    except Exception:
                        pass
                    self._update_resize_overlay_geometry()

                self._undo.push(_FnCommand("Scale Layer", lambda: _apply(after), lambda: _apply(before)))
            return

        if isinstance(layer, TextLayer) and isinstance(it, QGraphicsTextItem):
            before = int(self._resize_start_text_pt)
            try:
                after = int(getattr(layer, "font_point_size", before) or before)
            except Exception:
                after = before
            if after != before:

                def _apply(pt: int) -> None:
                    try:
                        layer.font_point_size = int(pt)
                    except Exception:
                        pass
                    try:
                        f = it.font()
                        f.setPointSize(int(pt))
                        it.setFont(f)
                    except Exception:
                        pass
                    self._update_resize_overlay_geometry()

                self._undo.push(_FnCommand("Scale Text", lambda: _apply(after), lambda: _apply(before)))
            return

    def _make_shape_preview(self, a: QPointF, b: QPointF) -> QGraphicsItem:
        a = self._clamp_to_canvas(a)
        b = self._clamp_to_canvas(b)
        r = QRectF(a, b).normalized()
        pen = QPen(self._brush_color)
        pen.setWidth(2)
        brush = QBrush(QColor(self._brush_color.red(), self._brush_color.green(), self._brush_color.blue(), 40))
        if self._tool == self.Tool.ELLIPSE:
            it = QGraphicsEllipseItem(r)
        else:
            it = QGraphicsRectItem(r)
        it.setPen(pen)
        it.setBrush(brush)
        it.setZValue(9999)
        self._scene.addItem(it)
        return it

    def _update_shape_preview(self, a: QPointF, b: QPointF) -> None:
        a = self._clamp_to_canvas(a)
        b = self._clamp_to_canvas(b)
        r = QRectF(a, b).normalized()
        if isinstance(self._shape_preview, (QGraphicsRectItem, QGraphicsEllipseItem)):
            self._shape_preview.setRect(r)

    def _commit_shape(self, a: QPointF, b: QPointF) -> None:
        layer = self._active_raster_layer()
        if layer is None:
            return
        item = self._layer_to_item.get(id(layer))
        if not isinstance(item, QGraphicsPixmapItem):
            return
        a = self._clamp_to_canvas(a)
        b = self._clamp_to_canvas(b)
        r = QRectF(a, b).normalized()
        img = layer.image
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(self._brush_color)
            pen.setWidth(2)
            p.setPen(pen)
            p.setBrush(QBrush(self._brush_color))
            if self._tool == self.Tool.ELLIPSE:
                p.drawEllipse(r)
            else:
                p.drawRect(r)
        finally:
            p.end()
        item.setPixmap(QPixmap.fromImage(img))
        self.changed.emit()

    # ---------------------- crop ----------------------

    def _commit_crop(self) -> None:
        if self._crop_preview is None:
            return
        try:
            r_scene = QRectF(self._crop_preview.rect()).normalized()
        except Exception:
            r_scene = QRectF()

        try:
            self._scene.removeItem(self._crop_preview)
        except Exception:
            pass
        self._crop_preview = None
        self._crop_start = None

        layer = self._active_layer
        if not isinstance(layer, ImageLayer):
            return
        item = self._layer_to_item.get(id(layer))
        if not isinstance(item, QGraphicsPixmapItem):
            return

        # Convert scene crop rect into source-image coordinates (approx, assumes only scale+pos).
        try:
            pos = QPointF(item.pos())
            s = float(item.transform().m11())
        except Exception:
            pos = QPointF(0.0, 0.0)
            s = float(getattr(layer, "scale", 1.0) or 1.0)
        s = max(1e-6, s)

        x0 = (r_scene.x() - pos.x()) / s
        y0 = (r_scene.y() - pos.y()) / s
        w0 = r_scene.width() / s
        h0 = r_scene.height() / s
        # Clamp to source.
        src = layer.source
        if src.isNull():
            return
        x0 = max(0.0, min(float(src.width() - 1), float(x0)))
        y0 = max(0.0, min(float(src.height() - 1), float(y0)))
        w0 = max(1.0, min(float(src.width()) - x0, float(w0)))
        h0 = max(1.0, min(float(src.height()) - y0, float(h0)))

        layer.crop_rect = QRectF(float(x0), float(y0), float(w0), float(h0))

        # Update displayed pixmap to cropped version.
        try:
            cropped = src.copy(int(x0), int(y0), int(w0), int(h0))
            item.setPixmap(QPixmap.fromImage(cropped))
        except Exception:
            pass

        self.changed.emit()


class _CanvasView(QGraphicsView):
    canvas_mouse_press = Signal(QPointF, object)
    canvas_mouse_move = Signal(QPointF, object)
    canvas_mouse_release = Signal(QPointF, object)
    canvas_wheel = Signal(QPointF, int, object)
    canvas_drop_image = Signal(str)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasUrls():
            for url in md.urls():
                p = url.toLocalFile()
                if not p:
                    continue
                ext = os.path.splitext(p)[1].lower().lstrip(".")
                if ext in ("png", "jpg", "jpeg", "webp", "bmp"):
                    self.canvas_drop_image.emit(p)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)

    def mousePressEvent(self, event) -> None:
        self.canvas_mouse_press.emit(self.mapToScene(event.position().toPoint()), event.button())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self.canvas_mouse_move.emit(self.mapToScene(event.position().toPoint()), event.buttons())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self.canvas_mouse_release.emit(self.mapToScene(event.position().toPoint()), event.button())
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        self.canvas_wheel.emit(self.mapToScene(event.position().toPoint()), int(event.angleDelta().y()), event.modifiers())
        super().wheelEvent(event)
