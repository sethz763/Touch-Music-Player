from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Slot, QTimer, Signal
from PySide6.QtWidgets import (
	QWidget,
	QVBoxLayout,
	QHBoxLayout,
	QStackedWidget,
	QPushButton,
	QSizePolicy,
)

from ui.widgets.button_bank_widget import ButtonBankWidget
from persistence.SaveSettings import SaveSettings


class BankSelectorWidget(QWidget):
	"""A 10-bank selector that hosts multiple ButtonBankWidget instances.

	UX:
	- Top row: 10 buttons selecting the active bank.
	- Body: a stacked widget containing 10 ButtonBankWidget grids.

	Behavior:
	- Each bank maintains its own button state.
	- All banks stay connected to the EngineAdapter so hidden banks still receive
	  cue updates; when switching banks we force a light UI refresh so the newly
	  visible bank immediately reflects current active state.
	"""

	bank_changed = Signal(int)

	def __init__(
		self,
		banks: int = 10,
		rows: int = 3,
		cols: int = 8,
		engine_adapter=None,
		parent: Optional[QWidget] = None,
	) -> None:
		super().__init__(parent)

		self.banks = int(banks)
		self.rows = int(rows)
		self.cols = int(cols)
		self.engine_adapter = engine_adapter

		# Persist all button assignments/settings for all banks.
		# Debounced writes prevent disk churn during rapid edits (multi-file drops, etc.).
		self._button_settings = SaveSettings("ButtonSettings.json", autosave=True, debounce_seconds=0.5)

		self._bank_buttons: list[QPushButton] = []
		self._bank_widgets: list[ButtonBankWidget] = []
		self._current_bank_index: int = 0

		root = QVBoxLayout(self)
		root.setContentsMargins(6, 6, 6, 6)
		root.setSpacing(6)

		# Top selector row
		selector_row = QHBoxLayout()
		selector_row.setContentsMargins(0, 0, 0, 0)
		selector_row.setSpacing(6)
		root.addLayout(selector_row)

		# Stacked banks
		self._stack = QStackedWidget()
		self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
		root.addWidget(self._stack, 1)

		for idx in range(self.banks):
			btn = QPushButton(f"BANK {idx}")
			btn.setCheckable(True)
			btn.setMinimumHeight(36)
			btn.clicked.connect(lambda checked=False, i=idx: self.set_current_bank(i))
			selector_row.addWidget(btn, 1)
			self._bank_buttons.append(btn)

			bank_widget = ButtonBankWidget(
				rows=self.rows,
				cols=self.cols,
				engine_adapter=self.engine_adapter,
				bank_index=idx,
				settings_store=self._button_settings,
			)
			self._bank_widgets.append(bank_widget)
			self._stack.addWidget(bank_widget)

		# Default to first bank
		if self._bank_buttons:
			self._bank_buttons[0].setChecked(True)
		self._stack.setCurrentIndex(0)

		# Defer persistence population + restore until Qt has had a chance to paint.
		# This avoids a white/unresponsive window during heavy restore (file probing, etc.).
		try:
			QTimer.singleShot(0, self._post_init_restore)
		except Exception:
			# Fall back to immediate behavior if timer isn't available.
			self._post_init_restore()

	def _post_init_restore(self) -> None:
		try:
			self._ensure_all_buttons_persisted()
		except Exception:
			pass
		try:
			self._refresh_visible_bank()
		except Exception:
			pass

	def _ensure_all_buttons_persisted(self) -> None:
		"""Populate ButtonSettings.json with entries for all banks/buttons.

		We only fill missing entries; we never overwrite existing button state.
		"""
		store = getattr(self, "_button_settings", None)
		if store is None:
			return
		try:
			root = getattr(store, "settings", None)
			if not isinstance(root, dict):
				return
			root.setdefault("schema", 1)
			banks = root.setdefault("banks", {})
			if not isinstance(banks, dict):
				return

			changed = False
			for bank_widget in self._bank_widgets:
				bank_idx = getattr(bank_widget, "bank_index", None)
				bank_key = str(bank_idx)
				bank_dict = banks.setdefault(bank_key, {})
				if not isinstance(bank_dict, dict):
					continue

				for btn in getattr(bank_widget, "buttons", []) or []:
					try:
						idx = getattr(btn, "index_in_bank", None)
						if idx is None:
							continue
						key = str(idx)
						if key in bank_dict:
							continue
						get_state = getattr(btn, "get_persisted_state", None)
						state = get_state() if callable(get_state) else {"file_path": None}
						bank_dict[key] = state if isinstance(state, dict) else {"file_path": None}
						changed = True
					except Exception:
						continue

			if changed:
				schedule = getattr(store, "schedule_save", None)
				if callable(schedule):
					schedule()
				else:
					save = getattr(store, "save_settings", None)
					if callable(save):
						save()
		except Exception:
			return

	@Slot(int)
	def set_current_bank(self, index: int) -> None:
		index = int(index)
		if index < 0 or index >= len(self._bank_widgets):
			return
		if index == self._current_bank_index:
			return

		self._current_bank_index = index
		self._stack.setCurrentIndex(index)

		# Keep selector buttons in sync.
		for i, btn in enumerate(self._bank_buttons):
			if btn.isChecked() != (i == index):
				btn.setChecked(i == index)

		# Force the newly visible bank to repaint/refresh label/time state.
		self._refresh_visible_bank()
		try:
			self.bank_changed.emit(int(index))
		except Exception:
			pass

	def current_bank_index(self) -> int:
		"""Return the currently visible bank index."""
		try:
			return int(self._current_bank_index)
		except Exception:
			return 0

	def current_bank(self) -> ButtonBankWidget:
		return self._bank_widgets[self._current_bank_index]

	def ensure_bank_restored(self, index: int) -> None:
		"""Ensure a bank's buttons are restored/populated even if the bank is hidden.

		This is used by external renderers (e.g. Stream Deck) that need accurate
		labels/state without forcing the GUI to switch visible banks.
		"""
		try:
			index = int(index)
		except Exception:
			return
		if index < 0 or index >= len(self._bank_widgets):
			return
		bank = self._bank_widgets[index]
		try:
			ensure = getattr(bank, "ensure_restored", None)
			if callable(ensure):
				ensure()
		except Exception:
			pass

		# Best-effort: refresh button text immediately (file probing continues async).
		for btn in getattr(bank, "buttons", []) or []:
			try:
				btn._refresh_label()
			except Exception:
				pass
			try:
				btn.update()
			except Exception:
				pass

	# ---------------------------------------------------------------------
	# Pass-through helpers expected by MainWindow / PlayControls
	# ---------------------------------------------------------------------

	def transport_next(self) -> None:
		"""Play next cue in the currently visible bank."""
		try:
			self.current_bank().transport_next()
		except Exception:
			pass

	def transport_set_loop_for_active(self, enabled: bool) -> None:
		"""Set loop on/off for all currently playing cues across all banks."""
		for bank in self._bank_widgets:
			try:
				bank.transport_set_loop_for_active(bool(enabled))
			except Exception:
				continue

	# ---------------------------------------------------------------------
	# Internal UI refresh
	# ---------------------------------------------------------------------

	def _refresh_visible_bank(self) -> None:
		"""Best-effort refresh so visible bank reflects current cue state."""
		try:
			bank = self.current_bank()
		except Exception:
			return

		# Lazy restore so we don't probe every bank's audio files on startup.
		try:
			ensure = getattr(bank, "ensure_restored", None)
			if callable(ensure):
				ensure()
		except Exception:
			pass

		# Ensure the grid repaints.
		try:
			bank.update()
		except Exception:
			pass

		# Best-effort refresh for each SoundFileButton.
		for btn in getattr(bank, "buttons", []) or []:
			try:
				# Private method, but safe and fast; keeps label/time consistent.
				btn._refresh_label()
			except Exception:
				pass
			try:
				btn.update()
			except Exception:
				pass

	def flush_persistence(self) -> None:
		"""Force any pending debounced button settings writes to disk."""
		try:
			self._button_settings.flush()
		except Exception:
			pass

	def load_button_settings(self, settings_dict: dict) -> None:
		"""Replace all button persistence (used by project load)."""
		try:
			self._button_settings.replace_settings(settings_dict or {}, save=True)
		except Exception:
			pass

		# Make sure the loaded structure explicitly includes all buttons.
		self._ensure_all_buttons_persisted()

		# Force all banks to restore again from the new store.
		# ButtonBankWidget uses `_restored_from_disk` as its one-time restore flag.
		for bank in self._bank_widgets:
			try:
				if hasattr(bank, "_restored_from_disk"):
					bank._restored_from_disk = False
			except Exception:
				continue

		self._refresh_visible_bank()

	# ---------------------------------------------------------------------
	# Drag/drop overflow support (multi-file drops)
	# ---------------------------------------------------------------------

	def distribute_overflow_files(self, from_button: object, file_paths: list[str], preview: bool = False):
		"""Distribute overflow files into subsequent banks.

		Called by SoundFileButton when a multi-file drop exceeds the remaining
		buttons in the current bank.

		Args:
			preview: If True, do not modify any buttons; only report which would be overwritten.

		Returns:
			list[tuple[SoundFileButton, str]]: overwritten button + old path
		"""
		if not file_paths:
			return []

		try:
			start_bank = getattr(from_button, "bank_index", None)
			if start_bank is None:
				start_bank = self._current_bank_index
			start_bank = int(start_bank)
		except Exception:
			start_bank = self._current_bank_index

		overwritten = []
		next_bank_idx = start_bank + 1
		file_idx = 0

		while file_idx < len(file_paths) and next_bank_idx < len(self._bank_widgets):
			bank = self._bank_widgets[next_bank_idx]
			for btn in getattr(bank, "buttons", []) or []:
				if file_idx >= len(file_paths):
					break

				try:
					old = getattr(btn, "file_path", None)
					if old:
						overwritten.append((btn, old))

					fp = file_paths[file_idx]
					file_idx += 1
					if not preview:
						apply_new = getattr(btn, "_set_new_file", None)
						if callable(apply_new):
							apply_new(fp)
						else:
							btn.file_path = fp
							btn._probe_file_async(fp)
							btn._refresh_label()
				except Exception:
					# If one button fails, keep going.
					continue

			next_bank_idx += 1

		return overwritten
