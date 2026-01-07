from __future__ import annotations

import os
import time
from typing import Callable, Optional

from PySide6.QtCore import QFileSystemWatcher, QMimeData, QPoint, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QDrag, QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.layered_canvas_editor import LayeredCanvasEditor


_ASSET_DIR_NAME = os.path.join("assets", "button_images")
_SUPPORTED_ASSET_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _try_get_all_emojis() -> Optional[list[str]]:
    try:
        import emoji  # type: ignore

        return list(emoji.EMOJI_DATA.keys())
    except Exception:
        return None


def _emoji_category(emj: str) -> str:
    """Best-effort emoji categorization fallback (Unicode ranges + flag detection)."""

    if not emj:
        return "Other"

    # Flags are sequences of regional indicator symbols.
    try:
        if all(0x1F1E6 <= ord(ch) <= 0x1F1FF for ch in emj if ch.strip()):
            return "Flags"
    except Exception:
        pass

    try:
        cp = ord(emj[0])
    except Exception:
        return "Other"

    if 0x1F600 <= cp <= 0x1F64F:
        return "Smileys"
    if 0x1F300 <= cp <= 0x1F5FF:
        return "Symbols"
    if 0x1F680 <= cp <= 0x1F6FF:
        return "Transport"
    if 0x1F900 <= cp <= 0x1F9FF:
        return "Symbols"
    if 0x1FA70 <= cp <= 0x1FAFF:
        return "Symbols"
    if 0x2600 <= cp <= 0x26FF:
        return "Symbols"
    if 0x2700 <= cp <= 0x27BF:
        return "Dingbats"
    if 0x1F1E6 <= cp <= 0x1F1FF:
        return "Flags"
    return "Other"


class EmojiPickerPopup(QDialog):
    """Popup-style emoji picker with categories + search.

    Implementation uses view items (QListWidget items) instead of thousands of QPushButton widgets
    to keep Qt stable.
    """

    def __init__(
        self,
        parent: QWidget,
        *,
        emojis: list[str],
        emoji_shortcodes: dict[str, str],
        emoji_categories: dict[str, str],
        on_pick: Callable[[str], None],
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self._on_pick = on_pick
        self._emojis = emojis
        self._emoji_shortcodes = emoji_shortcodes
        self._emoji_categories = emoji_categories

        self._active_category = "All"
        self._search_text = ""
        self._filtered_emojis: list[str] = []
        self._populate_index = 0

        self._cell_w = 52
        self._cell_h = 44
        self._emoji_font = QFont()
        self._emoji_font.setPointSize(18)

        self._populate_timer = QTimer(self)
        self._populate_timer.setSingleShot(True)
        self._populate_timer.timeout.connect(self._populate_more)

        self.setWindowTitle("Emoji Picker")
        self.setMinimumSize(560, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search… (matches :shortcode:)")
        self._search.textChanged.connect(self._on_filter_changed)
        root.addWidget(self._search)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        self._category_list = QListWidget(self)
        self._category_list.setMaximumWidth(220)
        self._category_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._category_list.itemSelectionChanged.connect(lambda: self._on_filter_changed(""))
        body.addWidget(self._category_list, 0)

        self._emoji_view = QListWidget(self)
        self._emoji_view.setViewMode(QListWidget.ViewMode.IconMode)
        self._emoji_view.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._emoji_view.setMovement(QListWidget.Movement.Static)
        self._emoji_view.setSpacing(2)
        self._emoji_view.setUniformItemSizes(True)
        self._emoji_view.setWordWrap(True)
        self._emoji_view.setGridSize(QPixmap(self._cell_w, self._cell_h).size())
        self._emoji_view.setFont(self._emoji_font)
        self._emoji_view.itemClicked.connect(self._on_emoji_item_clicked)
        body.addWidget(self._emoji_view, 1)

        self._setup_categories()
        self._apply_filter()
        self._schedule_populate()

    def _on_emoji_item_clicked(self, it: QListWidgetItem) -> None:
        try:
            emj = str(it.data(Qt.ItemDataRole.UserRole) or "").strip()
        except Exception:
            emj = ""
        if not emj:
            return
        try:
            self._on_pick(emj)
        finally:
            self.close()

    def _setup_categories(self) -> None:
        cats = set([str(self._emoji_categories.get(e) or "Other") for e in self._emojis])
        fallback_order = ["All", "Smileys", "Symbols", "Transport", "Dingbats", "Flags", "Other"]
        using_fallback = cats.issubset(set(fallback_order[1:]))

        self._category_list.blockSignals(True)
        try:
            self._category_list.clear()
            self._category_list.addItem(QListWidgetItem("All"))
            if using_fallback:
                for c in fallback_order[1:]:
                    if c in cats:
                        self._category_list.addItem(QListWidgetItem(c))
            else:
                for c in sorted([c for c in cats if c and c != "All"]):
                    self._category_list.addItem(QListWidgetItem(c))
            if self._category_list.count() > 0:
                self._category_list.setCurrentRow(0)
        finally:
            self._category_list.blockSignals(False)

    def _on_filter_changed(self, _txt: str) -> None:
        try:
            cur = self._category_list.currentItem()
            self._active_category = str(cur.text() if cur is not None else "All")
        except Exception:
            self._active_category = "All"
        try:
            self._search_text = str(self._search.text() or "").strip().lower()
        except Exception:
            self._search_text = ""

        self._apply_filter()
        self._emoji_view.clear()
        self._populate_index = 0
        self._schedule_populate()

    def _apply_filter(self) -> None:
        cat = self._active_category
        q = self._search_text

        out: list[str] = []
        for emj in self._emojis:
            if cat and cat != "All" and str(self._emoji_categories.get(emj) or "Other") != cat:
                continue
            if q:
                sc = (self._emoji_shortcodes.get(emj) or "").strip(":").lower()
                if q not in sc:
                    continue
            out.append(emj)

        self._filtered_emojis = out
        self._status.setText(f"{len(out)} emojis")

    def _schedule_populate(self) -> None:
        try:
            if self._populate_timer.isActive():
                self._populate_timer.stop()
            self._populate_timer.start(0)
        except Exception:
            QTimer.singleShot(0, self._populate_more)

    def _populate_more(self) -> None:
        batch = 1500
        start = self._populate_index
        end = min(len(self._filtered_emojis), start + batch)

        for idx in range(start, end):
            emj = self._filtered_emojis[idx]
            it = QListWidgetItem(emj)
            it.setData(Qt.ItemDataRole.UserRole, emj)
            sc = (self._emoji_shortcodes.get(emj) or "").strip()
            if sc:
                it.setToolTip(sc)
            self._emoji_view.addItem(it)

        self._populate_index = end
        if self._populate_index < len(self._filtered_emojis):
            self._schedule_populate()
        else:
            return


def _repo_root() -> str:
    try:
        return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    except Exception:
        return os.getcwd()


def _assets_dir() -> str:
    return os.path.join(_repo_root(), _ASSET_DIR_NAME)


class AssetListWidget(QListWidget):
    """Drag source for saved button images."""

    MIME_BG_ASSET = "application/x-stepd-button-bg-asset"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSpacing(6)
        self.setIconSize(QPixmap(96, 96).size())
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

    def startDrag(self, supportedActions) -> None:
        item = self.currentItem()
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return

        md = QMimeData()
        md.setData(self.MIME_BG_ASSET, str(path).encode("utf-8", errors="ignore"))
        # Also provide a URL so external drops work in other places.
        try:
            from PySide6.QtCore import QUrl

            md.setUrls([QUrl.fromLocalFile(str(path))])
        except Exception:
            pass

        drag = QDrag(self)
        drag.setMimeData(md)
        try:
            ico = item.icon()
            if not ico.isNull():
                drag.setPixmap(ico.pixmap(96, 96))
        except Exception:
            pass

        drag.exec(Qt.DropAction.CopyAction)


class ButtonImageDesignerWindow(QMainWindow):
    """In-app mini designer to create/edit button background images."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Button Image Designer")
        self.setMinimumSize(900, 650)

        os.makedirs(_assets_dir(), exist_ok=True)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_now)

        self._last_save_name: str = "button_image"

        # Central layout
        root = QWidget(self)
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)

        splitter = QSplitter(Qt.Orientation.Horizontal, root)
        root_layout.addWidget(splitter)

        # Toolbox (left)
        self._toolbox = QWidget(splitter)
        tb_layout = QVBoxLayout(self._toolbox)
        tb_layout.setContentsMargins(8, 8, 8, 8)

        tb_layout.addWidget(QLabel("Tools"))
        self._tool_buttons: dict[str, QToolButton] = {}

        def _add_tool(label: str, tool: str) -> None:
            b = QToolButton(self._toolbox)
            b.setText(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, t=tool: self._set_tool(t))
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tb_layout.addWidget(b)
            self._tool_buttons[tool] = b

        _add_tool("Select", LayeredCanvasEditor.Tool.SELECT)
        _add_tool("Move", LayeredCanvasEditor.Tool.MOVE)
        _add_tool("Brush", LayeredCanvasEditor.Tool.BRUSH)
        _add_tool("Eraser", LayeredCanvasEditor.Tool.ERASER)
        _add_tool("Rect", LayeredCanvasEditor.Tool.RECT)
        _add_tool("Ellipse", LayeredCanvasEditor.Tool.ELLIPSE)
        _add_tool("Text", LayeredCanvasEditor.Tool.TEXT)
        _add_tool("Emoji", LayeredCanvasEditor.Tool.EMOJI)
        _add_tool("Crop", LayeredCanvasEditor.Tool.CROP)

        tb_layout.addSpacing(8)
        undo_row = QHBoxLayout()
        undo_btn = QPushButton("Undo", self._toolbox)
        redo_btn = QPushButton("Redo", self._toolbox)
        undo_btn.clicked.connect(self._undo)
        redo_btn.clicked.connect(self._redo)
        undo_row.addWidget(undo_btn)
        undo_row.addWidget(redo_btn)
        tb_layout.addLayout(undo_row)

        self._undo_levels = QSpinBox(self._toolbox)
        self._undo_levels.setRange(0, 200)
        self._undo_levels.setValue(30)
        self._undo_levels.valueChanged.connect(lambda v: self._set_undo_levels(int(v)))
        tb_layout.addWidget(QLabel("Undo levels"))
        tb_layout.addWidget(self._undo_levels)

        tb_layout.addSpacing(10)
        tb_layout.addWidget(QLabel("Canvas"))

        self._size_combo = QComboBox(self._toolbox)
        self._size_combo.addItems(["128", "256", "512"])
        self._size_combo.setCurrentText("256")
        self._size_combo.currentTextChanged.connect(self._on_canvas_size_changed)
        tb_layout.addWidget(self._size_combo)

        self._brush_size = QSpinBox(self._toolbox)
        self._brush_size.setRange(1, 128)
        self._brush_size.setValue(10)
        self._brush_size.valueChanged.connect(lambda v: self._editor.set_brush_size(int(v)))
        tb_layout.addWidget(QLabel("Brush size"))
        tb_layout.addWidget(self._brush_size)

        self._color_btn = QPushButton("Brush Color…", self._toolbox)
        self._color_btn.clicked.connect(self._pick_brush_color)
        tb_layout.addWidget(self._color_btn)

        tb_layout.addSpacing(10)
        tb_layout.addWidget(QLabel("Text"))

        self._text_input = QLineEdit(self._toolbox)
        self._text_input.setPlaceholderText("Type text here…")
        tb_layout.addWidget(self._text_input)

        apply_text_btn = QPushButton("Apply Text", self._toolbox)
        apply_text_btn.clicked.connect(self._apply_typed_text)
        tb_layout.addWidget(apply_text_btn)

        tb_layout.addSpacing(10)
        tb_layout.addWidget(QLabel("Emoji Picker"))

        tip = QLabel("Tip: click the box below and press Win+. for the full emoji picker")
        tip.setWordWrap(True)
        tb_layout.addWidget(tip)

        self._emoji_input = QLineEdit(self._toolbox)
        self._emoji_input.setPlaceholderText("Type/paste emoji here…")
        tb_layout.addWidget(self._emoji_input)

        use_emoji_btn = QPushButton("Use Emoji", self._toolbox)
        use_emoji_btn.clicked.connect(self._use_typed_emoji)
        tb_layout.addWidget(use_emoji_btn)

        self._emoji_menu_btn = QPushButton("Emoji Menu…", self._toolbox)
        self._emoji_menu_btn.clicked.connect(self._open_emoji_menu)
        tb_layout.addWidget(self._emoji_menu_btn)

        tb_layout.addStretch(1)

        # Editor (center)
        self._editor = LayeredCanvasEditor(splitter, canvas_size=256)
        self._editor.changed.connect(self._on_editor_changed)
        self._editor.layers_changed.connect(self._refresh_layers_list)
        self._editor.active_layer_changed.connect(self._sync_layer_selection)

        # Right panel: layers + assets
        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)

        # Layers panel
        right_layout.addWidget(QLabel("Layers"))
        self._layers = QListWidget(right)
        self._layers.itemSelectionChanged.connect(self._on_layers_selection_changed)
        right_layout.addWidget(self._layers, 2)

        layer_btns = QHBoxLayout()
        add_raster = QPushButton("Add Raster", right)
        add_raster.clicked.connect(self._add_raster_layer)
        add_text = QPushButton("Add Text", right)
        add_text.clicked.connect(self._add_text_layer_dialog)
        remove = QPushButton("Remove", right)
        remove.clicked.connect(self._remove_selected_layer)
        layer_btns.addWidget(add_raster)
        layer_btns.addWidget(add_text)
        layer_btns.addWidget(remove)
        right_layout.addLayout(layer_btns)

        layer_btns2 = QHBoxLayout()
        up = QPushButton("Up", right)
        up.clicked.connect(lambda: self._move_layer(-1))
        down = QPushButton("Down", right)
        down.clicked.connect(lambda: self._move_layer(1))
        rename = QPushButton("Rename", right)
        rename.clicked.connect(self._rename_layer)
        toggle = QPushButton("Toggle Vis", right)
        toggle.clicked.connect(self._toggle_layer_visible)
        layer_btns2.addWidget(up)
        layer_btns2.addWidget(down)
        layer_btns2.addWidget(rename)
        layer_btns2.addWidget(toggle)
        right_layout.addLayout(layer_btns2)

        # Save/export
        right_layout.addWidget(QLabel("Export"))
        export_row = QHBoxLayout()
        self._name_edit = QLineEdit(right)
        self._name_edit.setPlaceholderText("name (no extension)")
        self._name_edit.setText(self._last_save_name)
        export_btn = QPushButton("Save PNG", right)
        export_btn.clicked.connect(self._save_png_dialog)
        export_row.addWidget(self._name_edit)
        export_row.addWidget(export_btn)
        right_layout.addLayout(export_row)

        # Assets browser
        right_layout.addWidget(QLabel("Assets (assets/button_images/)"))
        self._assets = AssetListWidget(right)
        right_layout.addWidget(self._assets, 3)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 1)

        self._set_tool(LayeredCanvasEditor.Tool.SELECT)
        self._refresh_layers_list()

        # Apply initial undo limit now that the editor exists.
        try:
            self._editor.set_undo_limit(int(self._undo_levels.value()))
        except Exception:
            pass

        # Shortcuts
        a_undo = QAction("Undo", self)
        a_undo.setShortcut("Ctrl+Z")
        a_undo.triggered.connect(self._undo)
        self.addAction(a_undo)

        a_redo = QAction("Redo", self)
        a_redo.setShortcut("Ctrl+Y")
        a_redo.triggered.connect(self._redo)
        self.addAction(a_redo)

        a_redo2 = QAction("Redo", self)
        a_redo2.setShortcut("Ctrl+Shift+Z")
        a_redo2.triggered.connect(self._redo)
        self.addAction(a_redo2)

        # Watcher for assets dir
        self._watcher = QFileSystemWatcher(self)
        try:
            self._watcher.addPath(_assets_dir())
        except Exception:
            pass
        self._watcher.directoryChanged.connect(lambda _p: self._refresh_assets())
        self._watcher.fileChanged.connect(lambda _p: self._refresh_assets())
        self._refresh_assets()

    # ---------------------- tools ----------------------

    def _set_tool(self, tool: str) -> None:
        self._editor.set_tool(tool)
        for t, btn in self._tool_buttons.items():
            btn.setChecked(t == tool)

    def _undo(self) -> None:
        try:
            self._editor.undo()
        except Exception:
            pass

    def _redo(self) -> None:
        try:
            self._editor.redo()
        except Exception:
            pass

    def _set_undo_levels(self, levels: int) -> None:
        try:
            self._editor.set_undo_limit(int(levels))
        except Exception:
            pass

    def _pick_brush_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        c = QColorDialog.getColor(QColor(255, 255, 255), self, "Brush Color")
        if c.isValid():
            self._editor.set_brush_color(c)

    def _on_canvas_size_changed(self, txt: str) -> None:
        try:
            size = int(str(txt))
        except Exception:
            return
        self._editor.set_canvas_size(size)

    # ---------------------- emojis ----------------------

    def _apply_typed_text(self) -> None:
        try:
            txt = (self._text_input.text() or "").strip()
        except Exception:
            txt = ""
        if not txt:
            return
        try:
            self._editor.set_pending_text(txt)
            self._set_tool(LayeredCanvasEditor.Tool.TEXT)
        except Exception:
            pass

    def _open_emoji_menu(self) -> None:
        emojis = _try_get_all_emojis()
        if not emojis:
            QMessageBox.warning(
                self,
                "Emoji Library Missing",
                "Couldn't import the 'emoji' library (or it has no EMOJI_DATA).\n"
                "Install it in the app environment (pip install emoji) and restart.",
            )
            return

        # Build maps for filtering/grouping.
        emoji_shortcodes: dict[str, str] = {}
        emoji_categories: dict[str, str] = {}
        try:
            import emoji as _emoji_mod  # type: ignore

            for e in emojis:
                try:
                    data = _emoji_mod.EMOJI_DATA.get(e, {})
                    emoji_shortcodes[e] = str(data.get("en", ""))
                    # Some emoji datasets include richer metadata like category/group.
                    # Use it if present, otherwise fall back to Unicode-range categorization.
                    cat = (
                        data.get("category")
                        or data.get("group")
                        or data.get("subcategory")
                        or data.get("subgroup")
                    )
                    emoji_categories[e] = str(cat) if cat else _emoji_category(e)
                except Exception:
                    emoji_shortcodes[e] = ""
                    emoji_categories[e] = _emoji_category(e)
        except Exception:
            emoji_shortcodes = {e: "" for e in emojis}
            emoji_categories = {e: _emoji_category(e) for e in emojis}

        def _picked(emj: str) -> None:
            try:
                self._emoji_input.setText(emj)
            except Exception:
                pass
            try:
                self._editor.set_pending_emoji(emj)
                self._set_tool(LayeredCanvasEditor.Tool.EMOJI)
            except Exception:
                pass

        popup = EmojiPickerPopup(
            self,
            emojis=emojis,
            emoji_shortcodes=emoji_shortcodes,
            emoji_categories=emoji_categories,
            on_pick=_picked,
        )
        try:
            anchor_btn = getattr(self, "_emoji_menu_btn", None)
            if anchor_btn is not None:
                gpos = anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height()))
            else:
                gpos = self.mapToGlobal(QPoint(40, 120))

            # Keep popup on-screen.
            try:
                screen = QApplication.screenAt(gpos)
            except Exception:
                screen = None
            if screen is None:
                try:
                    screen = QApplication.primaryScreen()
                except Exception:
                    screen = None
            if screen is not None:
                ag = screen.availableGeometry()
                x = max(ag.left(), min(gpos.x(), ag.right() - popup.width()))
                y = max(ag.top(), min(gpos.y(), ag.bottom() - popup.height()))
                popup.move(QPoint(int(x), int(y)))
            else:
                popup.move(gpos)
        except Exception:
            pass
        popup.show()

    def _use_typed_emoji(self) -> None:
        """Use whatever the user typed/pasted (supports multi-codepoint emoji sequences)."""
        try:
            em = (self._emoji_input.text() or "").strip()
        except Exception:
            em = ""
        if not em:
            return
        try:
            self._editor.set_pending_emoji(em)
            self._set_tool(LayeredCanvasEditor.Tool.EMOJI)
        except Exception:
            pass

    # (The old small emoji quick-list was replaced by the popup menu picker.)

    # ---------------------- layers panel ----------------------

    def _refresh_layers_list(self) -> None:
        self._layers.blockSignals(True)
        try:
            self._layers.clear()
            for layer in reversed(list(self._editor.doc.layers)):
                # show top-most first
                label = f"{layer.name}{'' if layer.visible else ' (hidden)'}"
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, layer)
                self._layers.addItem(it)
        finally:
            self._layers.blockSignals(False)
        self._sync_layer_selection(self._editor.active_layer())

    def _sync_layer_selection(self, layer) -> None:
        if layer is None:
            return
        for i in range(self._layers.count()):
            it = self._layers.item(i)
            if it.data(Qt.ItemDataRole.UserRole) is layer:
                self._layers.setCurrentRow(i)
                return

    def _selected_layer(self):
        it = self._layers.currentItem()
        if it is None:
            return None
        return it.data(Qt.ItemDataRole.UserRole)

    def _on_layers_selection_changed(self) -> None:
        layer = self._selected_layer()
        if layer is not None:
            self._editor.set_active_layer(layer)

    def _add_raster_layer(self) -> None:
        layer = self._editor.add_raster_layer("Raster")
        self._editor.set_active_layer(layer)
        self._refresh_layers_list()

    def _add_text_layer_dialog(self) -> None:
        txt, ok = QInputDialog.getText(self, "Add Text", "Text:")
        if not ok:
            return
        layer = self._editor.add_text_layer(txt, name="Text")
        self._editor.set_active_layer(layer)
        self._refresh_layers_list()

    def _remove_selected_layer(self) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        self._editor.remove_layer(layer)
        self._refresh_layers_list()

    def _move_layer(self, delta: int) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        # List is reversed; invert movement.
        self._editor.reorder_layer(layer, -int(delta))
        self._refresh_layers_list()

    def _rename_layer(self) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Layer", "Name:", text=str(getattr(layer, "name", "Layer")))
        if not ok:
            return
        self._editor.rename_layer(layer, name)
        self._refresh_layers_list()

    def _toggle_layer_visible(self) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        self._editor.set_layer_visible(layer, not bool(getattr(layer, "visible", True)))
        self._refresh_layers_list()

    # ---------------------- assets browser ----------------------

    def _refresh_assets(self) -> None:
        self._assets.clear()
        root = _assets_dir()
        try:
            names = sorted([n for n in os.listdir(root) if n.lower().endswith(".png")])
        except Exception:
            names = []

        for n in names[:256]:
            path = os.path.join(root, n)
            img = QImage(path)
            if img.isNull():
                continue
            thumb = QPixmap.fromImage(img.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            it = QListWidgetItem(QIcon(thumb), os.path.splitext(n)[0])
            it.setData(Qt.ItemDataRole.UserRole, path)
            self._assets.addItem(it)

    # ---------------------- saving / autosave (Step C completes logic) ----------------------

    def _on_editor_changed(self) -> None:
        # Debounced autosave every ~3 seconds.
        self._autosave_timer.start(3000)

    def _autosave_now(self) -> None:
        # Save to _autosave.png (best-effort).
        try:
            out = os.path.join(_assets_dir(), "_autosave.png")
            img = self._editor.export_composite()
            img.save(out, "PNG")
        except Exception:
            pass

    def _save_png_dialog(self) -> None:
        name = (self._name_edit.text() or "").strip()
        if not name:
            QMessageBox.information(self, "Name Required", "Enter a name (no extension).")
            return
        safe = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_"))
        if not safe:
            QMessageBox.information(self, "Invalid Name", "Name must include letters/numbers.")
            return

        out = os.path.join(_assets_dir(), safe + ".png")
        try:
            img = self._editor.export_composite()
            ok = img.save(out, "PNG")
            if not ok:
                raise RuntimeError("QImage.save returned False")
            self._last_save_name = safe
            self._name_edit.setText(safe)
            self._refresh_assets()
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Failed to save PNG:\n{e}")
