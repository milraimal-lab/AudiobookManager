"""The main window: trees, tabs, toolbar, and all the orchestration between them."""

from pathlib import Path
from typing import Optional, List
import json
import re
import shutil
import sys

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLabel, QLineEdit, QPushButton, QTextEdit,
    QFormLayout, QScrollArea, QGroupBox, QFileDialog, QMessageBox,
    QDialog, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QStatusBar, QFrame, QCheckBox, QMenu, QAbstractItemView,
    QToolBar, QTreeWidget, QTreeWidgetItem, QSizePolicy, QTabWidget,
    QComboBox, QSpinBox, QGridLayout, QInputDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QMimeData, QByteArray, QPoint, QTimer
from PyQt6.QtGui  import QPixmap, QAction, QColor, QDrag, QFont, QCursor, QPainter, QPen

import scanner as sc
import tagger as tg
import openlibrary as ol
import audible as au
import organizer as org

from constants import *
from util import (_load_settings, _save_settings, log_line, LOG_PATH,
                  send_to_recycle_bin, find_ffmpeg, fmt_size, fmt_duration)
from workers import (ScanThread, HydrateThread, SaveThread, DupCheckThread,
                     OrganizeThread, M4bThread)
from booktree import BookTreeWidget, _book_misplacement
from dialogs import ProblemsDialog, DupResultsDialog, ImportSettingsDialog
from tabs import EditMetadataTab, FilesTab, MoveOrganiseTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        settings = _load_settings()
        self.books:         List[sc.Book]    = []   # library books
        self.import_books:  List[sc.Book]    = []
        self.current_book:  Optional[sc.Book] = None
        self.last_folder:   str              = settings.get('library_folder', '')
        self.import_folder: str              = settings.get('import_folder', '')
        self.import_enabled: bool            = bool(settings.get('import_enabled', False))
        self._scan_thread:  Optional[ScanThread]     = None
        self._import_scan_thread: Optional[ScanThread] = None
        self._save_thread:  Optional[SaveThread]     = None
        self._org_thread:   Optional[OrganizeThread] = None
        self._m4b_thread:   Optional[QThread]        = None
        self._hydrate_threads: list = []
        self._dup_thread:   Optional[QThread]        = None
        self._author_scan_thread: Optional[ScanThread] = None
        self._author_scan_ctx: Optional[dict]        = None
        self._pending_move_ctx: Optional[dict]       = None
        self._suppress_cross_clear = False
        self._problems: dict = {'Library': [], 'Import': []}
        self._op_log:   list = []   # undo history for file operations
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(1100, 680); self.resize(1400, 860)
        self._build_ui()
        if self.last_folder and Path(self.last_folder).exists():
            QTimer.singleShot(0, lambda: self._start_scan(self.last_folder))
        if self.import_enabled and self.import_folder:
            QTimer.singleShot(0, lambda: self._start_import_scan(self.import_folder))
        self._set_status("Ready — open a folder with  Open Folder  (Ctrl+O)")

    def _build_ui(self):
        self._build_toolbar()
        central = QWidget(); self.setCentralWidget(central)
        rl = QVBoxLayout(central); rl.setContentsMargins(4, 4, 4, 4)
        ms = QSplitter(Qt.Orientation.Horizontal); rl.addWidget(ms)
        ms.addWidget(self._build_left_panel())
        ms.addWidget(self._build_right_panel())
        ms.setSizes([300, 1100])
        self.status_bar = QStatusBar(); self.setStatusBar(self.status_bar)
        self.unsaved_lbl = QLabel("")
        self.unsaved_lbl.setStyleSheet(f"color:{YELLOW}; font-weight:bold; padding:0 8px;")
        self.status_bar.addPermanentWidget(self.unsaved_lbl)
        self.prog_bar = QProgressBar(); self.prog_bar.setMaximumWidth(220)
        self.prog_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.prog_bar)

    def _build_toolbar(self):
        tb = QToolBar(); tb.setMovable(False); self.addToolBar(tb)
        for a in [
            ("Open Folder",    "Ctrl+O", self._open_folder),
            ("Import…",        "",       self._open_import_dialog),
            ("Rescan",         "F5",     self._rescan),
            None,
            ("Save All Tags",  "Ctrl+S", self._save_all),
            None,
            ("Delete Empty Folders", "", self._delete_empty_folders),
        ]:
            if a is None: tb.addSeparator()
            else:
                label, sc_, slot = a
                act = QAction(label, self)
                if sc_: act.setShortcut(sc_)
                act.triggered.connect(slot); tb.addAction(act)
                if label == "Import…":
                    act.setToolTip("Configure a separate Import folder for unsorted books.")

        tb.addSeparator()
        self.undo_act = QAction("Undo File Op", self)
        self.undo_act.setEnabled(False)
        self.undo_act.setToolTip("Undo the last file move / copy / rename")
        self.undo_act.triggered.connect(self._undo_last_op)
        tb.addAction(self.undo_act)

        tb.addSeparator()
        self.problems_act = QAction("⚠ Problems", self)
        self.problems_act.setEnabled(False)
        self.problems_act.setToolTip("Files that failed to read during scanning")
        self.problems_act.triggered.connect(self._show_problems)
        tb.addAction(self.problems_act)

        dup_act = QAction("Find Duplicates", self)
        dup_act.setToolTip("Compare books sharing a title: file sizes, then MD5 content hashes")
        dup_act.triggered.connect(self._find_duplicates)
        tb.addAction(dup_act)

        log_act = QAction("Log", self)
        log_act.setToolTip("Open the session log (every action + file operation)")
        log_act.triggered.connect(self._open_log)
        tb.addAction(log_act)

    def _build_left_panel(self) -> QWidget:
        w = QWidget(); w.setMinimumWidth(240); w.setMaximumWidth(420)
        self._left_panel = w
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter books…")
        self.search_edit.textChanged.connect(self._filter_books)
        lay.addWidget(self.search_edit)

        self._panel_splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Import section (hidden by default) ──────────────────────
        self._import_group = QWidget()
        il = QVBoxLayout(self._import_group); il.setContentsMargins(0, 0, 0, 0); il.setSpacing(2)
        ihdr_row = QHBoxLayout()
        ihdr = QLabel("📥  Import")
        ihdr.setStyleSheet(f"color:{PEACH}; font-weight:bold; padding:2px 4px;")
        ihdr_row.addWidget(ihdr)
        for sym, tip, slot in [("−", "Collapse all authors",
                                lambda: self.import_tree.collapseAll()),
                               ("+", "Expand all authors",
                                lambda: self.import_tree.expandAll())]:
            b = QPushButton(sym); b.setFixedSize(24, 22); b.setToolTip(tip)
            b.setStyleSheet("padding:0; font-weight:bold;")
            b.clicked.connect(slot); ihdr_row.addWidget(b)
        ihdr_row.addStretch()
        organize_btn = QPushButton("Organize All → Library")
        organize_btn.setToolTip(
            "Move every import book that has an author into the Library folder structure")
        organize_btn.clicked.connect(self._organize_all_imports)
        ihdr_row.addWidget(organize_btn)
        il.addLayout(ihdr_row)
        self.import_count_lbl = QLabel("Import folder not loaded")
        self.import_count_lbl.setStyleSheet(f"color:{GRAY}; font-size:11px; padding:2px 4px;")
        il.addWidget(self.import_count_lbl)
        self.import_tree = BookTreeWidget()
        self.import_tree.book_lookup = self._find_book_anywhere
        self.import_tree.book_selected.connect(self._on_book_selected)
        self.import_tree.selection_changed.connect(self._on_selection_changed)
        self.import_tree.files_moved.connect(self._on_files_moved)
        self.import_tree.books_metadata_changed.connect(self._on_books_metadata_changed)
        self.import_tree.book_created.connect(self._on_book_created)
        self.import_tree.books_imported.connect(
            lambda ids: self._on_books_imported(ids, to_library=False))
        self.import_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.import_tree.customContextMenuRequested.connect(self._book_context_menu)
        self.import_tree.itemSelectionChanged.connect(self._on_import_tree_selection)
        il.addWidget(self.import_tree)
        self._panel_splitter.addWidget(self._import_group)
        self._import_group.setVisible(False)

        # ── Library section ─────────────────────────────────────────
        lib_w = QWidget()
        ll = QVBoxLayout(lib_w); ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(2)
        lhdr_row = QHBoxLayout()
        lhdr = QLabel("📚  Library")
        lhdr.setStyleSheet(f"color:{LAVENDER}; font-weight:bold; padding:2px 4px;")
        lhdr_row.addWidget(lhdr)
        for sym, tip, slot in [("−", "Collapse all authors",
                                lambda: self.book_tree.collapseAll()),
                               ("+", "Expand all authors",
                                lambda: self.book_tree.expandAll())]:
            b = QPushButton(sym); b.setFixedSize(24, 22); b.setToolTip(tip)
            b.setStyleSheet("padding:0; font-weight:bold;")
            b.clicked.connect(slot); lhdr_row.addWidget(b)
        lhdr_row.addStretch()
        ll.addLayout(lhdr_row)
        self.count_lbl = QLabel("No books loaded")
        self.count_lbl.setStyleSheet(f"color:{GRAY}; font-size:11px; padding:2px 4px;")
        ll.addWidget(self.count_lbl)
        self.book_tree = BookTreeWidget()
        self.book_tree.book_lookup = self._find_book_anywhere
        self.book_tree.book_selected.connect(self._on_book_selected)
        self.book_tree.selection_changed.connect(self._on_selection_changed)
        self.book_tree.files_moved.connect(self._on_files_moved)
        self.book_tree.books_metadata_changed.connect(self._on_books_metadata_changed)
        self.book_tree.book_created.connect(self._on_book_created)
        self.book_tree.books_imported.connect(
            lambda ids: self._on_books_imported(ids, to_library=True))
        # Dropping a book on a library author/series physically files it there
        self.book_tree.books_organize_requested.connect(self._on_books_dropped_library)
        self.book_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.book_tree.customContextMenuRequested.connect(self._book_context_menu)
        self.book_tree.itemSelectionChanged.connect(self._on_library_tree_selection)
        ll.addWidget(self.book_tree)
        note = QLabel("Drag books between trees or onto authors/series  •  Right-click for move, merge & rename")
        note.setStyleSheet(f"color:{GRAY}; font-size:10px; padding:2px 4px;"); note.setWordWrap(True)
        ll.addWidget(note)
        self._panel_splitter.addWidget(lib_w)
        self._panel_splitter.setSizes([220, 520])

        lay.addWidget(self._panel_splitter)
        return w

    def _build_right_panel(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(4, 0, 0, 0)

        self.book_header = QLabel("Select a book from the tree")
        self.book_header.setStyleSheet(f"font-size:15px; font-weight:bold; color:{BLUE}; padding:4px 0;")
        lay.addWidget(self.book_header)

        self.tabs = QTabWidget()

        self.path_btn = QPushButton("")
        self.path_btn.setFlat(True)
        self.path_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.path_btn.setStyleSheet(
            f"color:{GRAY}; font-family:Consolas; font-size:10px; padding:2px 8px;"
            "text-align:right; border:none;")
        self.path_btn.setVisible(False)
        self.path_btn.clicked.connect(self._open_current_book_folder)
        self.tabs.setCornerWidget(self.path_btn, Qt.Corner.TopRightCorner)

        self.meta_tab = EditMetadataTab()
        self.meta_tab.all_books_provider = lambda: self.books + self.import_books
        self.meta_tab.save_books_requested.connect(self._save_books)
        self.meta_tab.status_message.connect(self._set_status)
        self.meta_tab.tree_refresh_requested.connect(
            lambda: self._populate_both_trees(keep_expanded=True))
        self.meta_tab.fields_applied.connect(self._relocate_books_if_needed)
        self.tabs.addTab(self.meta_tab, "Edit Metadata")

        self.files_tab = FilesTab()
        self.files_tab.status_message.connect(self._set_status)
        self.files_tab.merge_requested.connect(self._merge_books)
        self.files_tab.ops_performed.connect(self._log_op)
        self.files_tab.build_m4b_requested.connect(self._build_m4b)
        self.files_tab.split_requested.connect(self._split_files_to_new_book)
        self.files_tab.autosplit_requested.connect(self._autosplit_book)
        self.tabs.addTab(self.files_tab, "Files")

        self.move_tab = MoveOrganiseTab()
        self.move_tab.status_message.connect(self._set_status)
        self.move_tab.rescan_requested.connect(self._rescan)
        self.move_tab.ops_performed.connect(self._log_op)
        if self.last_folder:
            self.move_tab.set_default_destination(self.last_folder)
        self.tabs.addTab(self.move_tab, "Move / Organise")

        lay.addWidget(self.tabs)
        return w

    # ── tree helpers ──────────────────────────────────────────────

    def _populate_tree(self):
        self.book_tree.populate(self.books)
        n = len(self.books)
        self.count_lbl.setText(f"{n} book(s)" if n else "No books loaded")
        self._update_unsaved_indicator()

    def _populate_both_trees(self, keep_expanded: bool = True):
        # Same author+title detection works across the two trees
        self.book_tree.external_titles = {
            ((b.author or '').strip().lower(), (b.title or '').strip().lower())
            for b in self.import_books if (b.title or '').strip()}
        self.import_tree.external_titles = {
            ((b.author or '').strip().lower(), (b.title or '').strip().lower())
            for b in self.books if (b.title or '').strip()}
        self.book_tree.populate(self.books, keep_expanded=keep_expanded)
        self.count_lbl.setText(f"{len(self.books)} book(s)" if self.books else "No books loaded")
        if self.import_enabled:
            self.import_tree.populate(self.import_books, keep_expanded=keep_expanded)
            self.import_count_lbl.setText(
                f"{len(self.import_books)} import book(s)" if self.import_books
                else "Import folder empty")
        # Hide the import section when it has nothing to show (a scan may still
        # be streaming books in — the tree's own list covers that case)
        has_imports = bool(self.import_books) or bool(self.import_tree._books)
        self._import_group.setVisible(self.import_enabled and has_imports)
        self._update_unsaved_indicator()
        self._update_panel_width()

    def _update_panel_width(self):
        """Let the left panel grow up to the longest book title."""
        fm = self.book_tree.fontMetrics()
        longest = 0
        for b in self.books + self.import_books:
            longest = max(longest, fm.horizontalAdvance(b.display_name))
        self._left_panel.setMaximumWidth(max(420, min(1000, longest + 200)))

    def _update_unsaved_indicator(self):
        n = sum(1 for b in (self.books + self.import_books) if b.modified)
        self.unsaved_lbl.setText(f"●  {n} unsaved — Ctrl+S to save" if n else "")

    # ── problems ─────────────────────────────────────────────────

    def _set_problems(self, source: str, problems: list):
        self._problems[source] = problems
        n = sum(len(v) for v in self._problems.values())
        self.problems_act.setText(f"⚠ Problems ({n})" if n else "⚠ Problems")
        self.problems_act.setEnabled(n > 0)

    def _show_problems(self):
        combined = [(src, path, err)
                    for src, plist in self._problems.items()
                    for path, err in plist]
        if combined:
            ProblemsDialog(combined, self).exec()

    # ── duplicate finder ─────────────────────────────────────────

    def _find_duplicates(self):
        all_books = self.books + self.import_books
        titles = {}
        for b in all_books:
            key = ((b.author or '').strip().lower(), (b.title or '').strip().lower())
            if key[1]: titles[key] = titles.get(key, 0) + 1
        if not any(c > 1 for c in titles.values()):
            QMessageBox.information(self, "Find Duplicates",
                "No two books by the same author share a title — nothing to compare.")
            return
        self._run_dup_check(all_books)

    def _find_duplicates_scoped(self, book: sc.Book):
        """Right-click a book → compare it against its same author+title twins."""
        key = ((book.author or '').strip().lower(), (book.title or '').strip().lower())
        if not key[1]:
            QMessageBox.information(self, "Find Duplicates",
                "This book has no title to match on.")
            return
        candidates = [b for b in self.books + self.import_books
                      if ((b.author or '').strip().lower(),
                          (b.title or '').strip().lower()) == key]
        if len(candidates) < 2:
            QMessageBox.information(self, "Find Duplicates",
                f"No other book by {book.author or 'this author'} is titled "
                f"'{book.title or book.display_name}'.")
            return
        self._run_dup_check(candidates)

    def _find_duplicates_author(self, author: str):
        """Right-click an author → check all their books (Library + Import)."""
        target = '' if author == 'Unknown Author' else author.strip().lower()
        candidates = [b for b in self.books + self.import_books
                      if (b.author or '').strip().lower() == target]
        counts = {}
        for b in candidates:
            t = (b.title or '').strip().lower()
            if t: counts[t] = counts.get(t, 0) + 1
        if not any(c > 1 for c in counts.values()):
            QMessageBox.information(self, "Find Duplicates",
                f"No duplicate titles found for {author}.")
            return
        self._run_dup_check(candidates)

    def _run_dup_check(self, books: List[sc.Book]):
        if self._dup_thread and self._dup_thread.isRunning():
            self._set_status("Duplicate check already running…"); return
        self._dup_thread = DupCheckThread(books)
        self._dup_thread.progress.connect(self._set_status)
        self._dup_thread.finished.connect(self._on_dup_check_done)
        self._dup_thread.start()
        self._set_status("Checking duplicates (size compare, then MD5)…")

    def _on_dup_check_done(self, results: list):
        self._set_status("Duplicate check finished.")
        for verdict, a, b, sa, sb in results:
            log_line(
                f"[dupcheck] {verdict} | '{a.display_name}' | "
                f"A: {a.files[0].path.parent} "
                f"({fmt_size(sa['bytes'])}, {fmt_duration(sa['seconds'])}) | "
                f"B: {b.files[0].path.parent} "
                f"({fmt_size(sb['bytes'])}, {fmt_duration(sb['seconds'])})")
        if not results:
            QMessageBox.information(self, "Duplicate Check",
                "No same author+title pairs found.")
            return
        DupResultsDialog(results, self._delete_books_from_disk, self).exec()

    def _delete_books_from_disk(self, books: List[sc.Book]) -> bool:
        """Move the books' files to the Recycle Bin and drop them from the
        lists. No confirmation popup — the Bin is the safety net."""
        books = [b for b in books if b.files]
        if not books: return False
        paths = [af.path for b in books for af in b.files]
        send_to_recycle_bin(paths)

        gone = [p for p in paths if not p.exists()]
        for p in gone:
            log_line(f"RECYCLED: {p}")
        for b in books:
            if any(af.path.exists() for af in b.files):
                continue   # something survived — keep the book listed
            for lst in (self.books, self.import_books):
                if b in lst: lst.remove(b)
            for tr in (self.book_tree, self.import_tree):
                if b in tr._books: tr._books.remove(b)
            if self.current_book is b:
                self.current_book = None
                self.files_tab.set_book(None)
        self._cleanup_source_folders(gone)
        self._populate_both_trees(keep_expanded=True)
        if len(gone) < len(paths):
            QMessageBox.warning(self, "Some Files Not Recycled",
                f"{len(paths) - len(gone)} file(s) couldn't be moved to the "
                "Recycle Bin (locked, or on a drive without one?). "
                "They were left untouched.")
        self._set_status(f"♻ Moved {len(gone)} file(s) to the Recycle Bin.")
        return bool(gone)

    # ── undo log ─────────────────────────────────────────────────

    def _log_op(self, desc: str, pairs: list, is_copy: bool):
        """Record a completed file operation. pairs = [(src, dst), …]"""
        if not pairs: return
        self._op_log.append({'desc': desc, 'pairs': pairs, 'copy': is_copy})
        del self._op_log[:-20]   # keep the last 20 operations
        self._update_undo_action()

    def _update_undo_action(self):
        if self._op_log:
            self.undo_act.setEnabled(True)
            self.undo_act.setText(f"Undo: {self._op_log[-1]['desc']}")
        else:
            self.undo_act.setEnabled(False)
            self.undo_act.setText("Undo File Op")

    def _undo_last_op(self):
        if not self._op_log: return
        op = self._op_log[-1]
        n = len(op['pairs'])
        if op['copy']:
            ans = QMessageBox.question(self, "Undo Copy",
                f"Undoing '{op['desc']}' will DELETE the {n} copied file(s).\n"
                "The originals are untouched. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes: return
            errors = []
            for src, dst in op['pairs']:
                try: Path(dst).unlink(missing_ok=True)
                except Exception as e: errors.append(f"{Path(dst).name}: {e}")
        else:
            ans = QMessageBox.question(self, "Undo Move / Rename",
                f"Move {n} file(s) back to their previous locations?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes: return
            errors = []
            for src, dst in reversed(op['pairs']):
                s, d = Path(src), Path(dst)
                try:
                    if d.exists():
                        if s.exists():   # never overwrite, even on undo
                            errors.append(f"{s.name}: original location occupied — skipped")
                            continue
                        s.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(d), str(s))
                except Exception as e: errors.append(f"{d.name}: {e}")
        self._op_log.pop()
        self._update_undo_action()
        if errors:
            QMessageBox.warning(self, "Undo Issues", "\n".join(errors[:8]))
        self._set_status("Undo complete — rescanning…")
        self._rescan()

    def _find_book_anywhere(self, bid):
        for lst in (self.books, self.import_books):
            for b in lst:
                if b.id == bid: return b
        return None

    def _on_books_dropped_library(self, book_ids: list):
        """Drag-drop onto a library author/series → move the files there too."""
        books = []
        for bid in book_ids:
            b = self._find_book_anywhere(bid)
            if b is not None: books.append(b)
        if books:
            self._start_book_move(books, use_series=True)

    def _on_books_imported(self, book_ids: list, to_library: bool):
        """A book was dropped from one tree into the other — re-home it."""
        src_books = self.import_books if to_library else self.books
        src_tree  = self.import_tree if to_library else self.book_tree
        moved = [b for b in src_books if b.id in book_ids]
        for b in moved:
            try: src_books.remove(b)
            except ValueError: pass
            if b in src_tree._books:
                src_tree._books.remove(b)
        self._populate_both_trees(keep_expanded=True)
        where = "Library" if to_library else "Import"
        self._set_status(f"Moved {len(moved)} book(s) → {where}")

    def _filter_books(self, text):
        self.book_tree.apply_filter(text)
        self.import_tree.apply_filter(text)

    # ── toolbar actions ───────────────────────────────────────────

    def _open_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Open Audiobook Folder", self.last_folder or str(Path.home()))
        if folder:
            self.last_folder = folder
            self.move_tab.set_default_destination(folder)
            self._persist_settings()
            self._start_scan(folder)

    def _rescan(self):
        if self.last_folder: self._start_scan(self.last_folder)
        if self.import_enabled and self.import_folder:
            self._start_import_scan(self.import_folder)

    def _open_import_dialog(self):
        dlg = ImportSettingsDialog(self.import_enabled, self.import_folder, self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        enabled, folder = dlg.get()

        was_enabled = self.import_enabled
        prev_folder = self.import_folder
        self.import_enabled = enabled and bool(folder)
        self.import_folder = folder
        self._persist_settings()

        if not self.import_enabled:
            self._import_group.setVisible(False)
            self._stop_import_scan()
            self.import_books.clear()
            self.import_tree.clear()
            self.import_count_lbl.setText("Import folder not loaded")
            return

        if self.import_enabled and folder and (not was_enabled or folder != prev_folder):
            self._start_import_scan(folder)

    def _start_import_scan(self, folder: str):
        self._stop_import_scan()
        self._stop_hydration()
        self.import_books.clear()
        self.import_tree.clear()
        self.import_count_lbl.setText("Scanning import folder…")
        self._import_scan_thread = ScanThread(folder)
        self._import_scan_thread.progress.connect(self._on_import_scan_progress)
        self._import_scan_thread.book_ready.connect(self._on_import_book_ready)
        self._import_scan_thread.problems_found.connect(
            lambda p: self._set_problems('Import', p))
        self._import_scan_thread.finished.connect(self._on_import_scan_done)
        self._import_scan_thread.error.connect(self._on_error)
        self._import_scan_thread.start()

    def _stop_import_scan(self):
        t = self._import_scan_thread
        if t and t.isRunning():
            try: t.requestInterruption()
            except Exception: pass
            t.wait(200)
        self._import_scan_thread = None

    def _on_import_scan_progress(self, cur, tot, msg):
        self._set_status(f"[Import] {msg}")

    def _on_import_book_ready(self, book: sc.Book):
        if not self._import_group.isVisible():
            self._import_group.setVisible(True)
        self.import_books.append(book)
        self.import_tree.add_book(book)
        self.import_count_lbl.setText(f"{len(self.import_tree._books)} book(s) found…")
        # Author folder is created in the LIBRARY root, not the import folder
        self._ensure_author_folder(book)

    def _on_import_scan_done(self, books):
        self.import_books.sort(
            key=lambda b: ((b.author or '').lower(), (b.title or '').lower()))
        self._populate_both_trees(keep_expanded=True)
        self._set_status(
            f"Import loaded — {len(self.import_books)} book(s) from {self.import_folder}")
        self._start_hydration(list(self.import_books))

    def _organize_all_imports(self):
        if not self.last_folder:
            QMessageBox.warning(self, "No Library", "Open a Library folder first (Ctrl+O).")
            return
        candidates = [b for b in self.import_books if b.author]
        no_author  = len(self.import_books) - len(candidates)
        if not candidates:
            QMessageBox.information(self, "Nothing to Organize",
                "No import books have an author set.\n"
                "Fill in authors first (right-click an author group → Set / rename author).")
            return

        # Skip conflicts: same author+title already in the library, or a
        # destination folder that already contains audio
        lib_keys = {((b.author or '').strip().lower(), (b.title or '').strip().lower())
                    for b in self.books if (b.title or '').strip()}
        clean, conflicts = [], []
        for b in candidates:
            key = ((b.author or '').strip().lower(), (b.title or '').strip().lower())
            if key[1] and key in lib_keys:
                conflicts.append((b, "already in library (same author + title)"))
                continue
            target = org.build_folder_path(
                self.last_folder, b.author, b.series, b.series_num,
                b.title or b.display_name)
            try:
                dest_taken = target.exists() and any(
                    p.suffix.lower() in sc.AUDIO_EXTENSIONS for p in target.iterdir())
            except OSError:
                dest_taken = False
            if dest_taken:
                conflicts.append((b, "destination folder already has audio"))
                continue
            clean.append(b)

        if not clean:
            lines = "\n".join(f"  • {b.display_name} — {why}" for b, why in conflicts[:8])
            QMessageBox.information(self, "Nothing to Organize",
                f"All {len(conflicts)} book(s) have conflicts:\n{lines}\n\n"
                "Resolve them with Find Duplicates or move them individually.")
            return

        msg = (f"Move {len(clean)} book(s) into the Library structure?\n\n"
               f"{self.last_folder}\\Author\\[Series]\\Title")
        details = []
        if conflicts:
            details.append(f"{len(conflicts)} conflict(s) will be SKIPPED:")
            details += [f"  • {b.display_name} — {why}" for b, why in conflicts[:6]]
            if len(conflicts) > 6: details.append("  …")
        if no_author:
            details.append(f"{no_author} book(s) without an author will be skipped")
        if details:
            msg += "\n\n" + "\n".join(details)
        ans = QMessageBox.question(self, "Organize All", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes: return
        for b, why in conflicts:
            log_line(f"[organize-all] skipped '{b.display_name}': {why}")
        self._move_books_batch(clean)

    def _on_import_tree_selection(self):
        if self._suppress_cross_clear: return
        if self.import_tree.selectedItems():
            self._suppress_cross_clear = True
            self.book_tree.clearSelection()
            self._suppress_cross_clear = False

    def _on_library_tree_selection(self):
        if self._suppress_cross_clear: return
        if self.book_tree.selectedItems():
            self._suppress_cross_clear = True
            self.import_tree.clearSelection()
            self._suppress_cross_clear = False

    def _persist_settings(self):
        _save_settings({
            'library_folder': self.last_folder,
            'import_folder':  self.import_folder,
            'import_enabled': self.import_enabled,
        })

    def _start_hydration(self, books):
        t = HydrateThread(books)
        t.book_hydrated.connect(self._on_book_hydrated)
        t.start()
        self._hydrate_threads.append(t)

    def _stop_hydration(self):
        for t in self._hydrate_threads:
            if t.isRunning():
                t.stop(); t.wait(500)
        self._hydrate_threads = [t for t in self._hydrate_threads if t.isRunning()]

    def _on_book_hydrated(self, book: sc.Book):
        tree = self.book_tree if book in self.books else self.import_tree
        tree.refresh_book(book)
        if self.current_book is book:
            self.files_tab.refresh()

    def _start_scan(self, folder):
        if self._scan_thread and self._scan_thread.isRunning(): return
        self._stop_hydration()
        self.books.clear(); self.current_book = None
        self.book_tree.clear(); self.meta_tab.set_books([])
        self.files_tab.set_book(None)
        self.book_header.setText("Scanning…")
        self.prog_bar.setRange(0, 0); self.prog_bar.setVisible(True)
        self._set_status(f"Scanning {folder}…")
        self._scan_thread = ScanThread(folder)
        self._scan_thread.progress.connect(self._on_scan_progress)
        self._scan_thread.book_ready.connect(self._on_book_ready)
        self._scan_thread.problems_found.connect(
            lambda p: self._set_problems('Library', p))
        self._scan_thread.finished.connect(self._on_scan_done)
        self._scan_thread.error.connect(self._on_error)
        self._scan_thread.start()

    def _on_scan_progress(self, cur, tot, msg):
        if tot > 0: self.prog_bar.setRange(0, tot); self.prog_bar.setValue(cur)
        self._set_status(msg)

    def _on_book_ready(self, book: sc.Book):
        """Called for each book as it is found — adds it to the tree immediately.
        self.books accumulates live so operations during the scan don't wipe
        the tree when something triggers a repopulate."""
        self.books.append(book)
        self.book_tree.add_book(book)
        n = len(self.book_tree._books)
        self.count_lbl.setText(f"{n} book(s) found…")
        self._ensure_author_folder(book)

    def _ensure_author_folder(self, book: sc.Book):
        """Create <library_root>/<Author>/ if missing. Files are NOT moved."""
        if not self.last_folder or not book.author:
            return
        safe = org.sanitize(book.author)
        if not safe or safe == 'Unknown':
            return
        target = Path(self.last_folder) / safe
        if target.exists():
            return
        try:
            target.mkdir(parents=True)
        except Exception:
            pass

    def _on_scan_done(self, books):
        # self.books was filled incrementally by _on_book_ready — mid-scan
        # merges/moves already acted on it, so just sort and repopulate.
        self.prog_bar.setVisible(False)
        self.books.sort(key=lambda b: ((b.author or '').lower(), (b.title or '').lower()))
        self._populate_both_trees(keep_expanded=True)
        self._set_status(f"Loaded {len(self.books)} book(s)  —  {self.last_folder}")
        self.book_header.setText("Select a book from the tree")
        self._start_hydration(list(self.books))

    def _save_books(self, books: List[sc.Book]):
        if not books: return
        self.prog_bar.setRange(0, 0); self.prog_bar.setVisible(True)
        self._save_thread = SaveThread(books)
        self._save_thread.progress.connect(self._on_save_progress)
        self._save_thread.finished.connect(lambda: self._on_save_done(books))
        self._save_thread.error.connect(self._on_error)
        self._save_thread.start()

    def _save_all(self):
        modified = [b for b in (self.books + self.import_books) if b.modified]
        if not modified: self._set_status("No unsaved changes."); return
        self._save_books(modified)

    def _on_save_progress(self, cur, tot, msg):
        self.prog_bar.setRange(0, tot); self.prog_bar.setValue(cur); self._set_status(msg)

    def _on_save_done(self, books: List[sc.Book]):
        self.prog_bar.setVisible(False)
        for b in books:
            b.modified = False
            # The explicit cover is now in every file's tags — from here on,
            # normal saves preserve per-file art again.
            b.cover_explicit = False
        self._populate_both_trees(keep_expanded=True)
        self._set_status(f"Tags saved for {len(books)} book(s) ✓")
        self._relocate_books_if_needed(books)

    def _relocate_books_if_needed(self, books: List[sc.Book]):
        """Metadata changed → move library books whose folder no longer
        matches their author/series/series #/title."""
        if not self.last_folder: return
        lib_root = Path(self.last_folder)
        stale = []
        for book in books:
            if book not in self.books: continue          # library books only
            if not book.author or not book.files: continue
            try:
                book.files[0].path.relative_to(lib_root)
            except ValueError:
                continue                                  # lives outside the library
            if _book_misplacement(book) == 'author':
                continue                                  # never organised — don't yank
            target = org.build_folder_path(
                self.last_folder, book.author, book.series, book.series_num,
                book.title or book.display_name)
            if book.files[0].path.parent != target:
                stale.append(book)
        if not stale: return
        if self._org_thread and self._org_thread.isRunning():
            return   # a move is running — the follow-up save catches it
        self._set_status(f"Folder out of date for {len(stale)} book(s) — relocating…")
        self._start_book_move(stale, use_series=True)

    def _merge_books(self, books: List[sc.Book]):
        """Merge 2+ books into the first one. Files are concatenated in the
        given order; track numbers will be reassigned on next Save All Tags."""
        if len(books) < 2:
            QMessageBox.information(self, "Merge",
                "Select 2 or more books to merge."); return
        primary = books[0]
        ans = QMessageBox.question(self, "Merge Books",
            f"Merge {len(books)} books into '{primary.display_name}'?\n\n"
            "All files will be combined into the first book in the order shown.\n"
            "Track numbers will be reassigned when you Save All Tags.\n"
            "Use the Files tab afterwards to reorder if needed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes: return

        for other in books[1:]:
            primary.files.extend(other.files)
            primary.modified = True
            for lst in (self.books, self.import_books):
                if other in lst: lst.remove(other)
            for tree in (self.book_tree, self.import_tree):
                if other in tree._books: tree._books.remove(other)

        self._populate_both_trees(keep_expanded=True)
        # Keep the merged book selected so the user can immediately reorder files
        if primary in self.books:    self.book_tree.select_book(primary)
        elif primary in self.import_books: self.import_tree.select_book(primary)
        self._set_status(
            f"Merged {len(books)} books → '{primary.display_name}' "
            f"({primary.file_count} files)")

    def _new_book_from(self, source: sc.Book, files: list) -> sc.Book:
        """Book pre-filled from *source*, refined by the files' own tags."""
        nb = sc.Book()
        nb.files    = files
        nb.author   = source.author
        nb.narrator = source.narrator
        nb.series   = source.series
        nb.genre    = source.genre
        t0 = files[0].tags or {}
        nb.title      = (t0.get('album') or t0.get('title') or files[0].path.stem)
        nb.series_num = t0.get('series_num', '') or ''
        nb.year       = t0.get('year', '') or t0.get('date', '') or source.year
        nb.publisher  = t0.get('publisher', '') or source.publisher
        nb.cover_art  = t0.get('cover_art') or source.cover_art
        nb.modified   = True
        return nb

    def _split_files_to_new_book(self, pairs: list):
        """Files tab 'Split selected' — move the chosen rows into a new book."""
        by_book: dict = {}
        for book, idx in pairs:
            by_book.setdefault(book, []).append(idx)
        created = []
        for book, indices in by_book.items():
            indices = sorted({i for i in indices if 0 <= i < book.file_count})
            if not indices: continue
            if len(indices) >= book.file_count:
                self._set_status(
                    f"'{book.display_name}': every file selected — nothing to split off.")
                continue
            moved = [book.files[i] for i in indices]
            for i in reversed(indices):
                book.files.pop(i)
            book.modified = True
            nb = self._new_book_from(book, moved)
            lst = self.books if book in self.books else self.import_books
            lst.append(nb)
            created.append(nb)
        if not created: return
        self._populate_both_trees(keep_expanded=True)
        last = created[-1]
        tree = self.book_tree if last in self.books else self.import_tree
        tree.select_book(last)
        self._set_status(
            f"Split off {len(created)} new book(s) — fix titles if needed, then Save.")

    def _autosplit_book(self, book: Optional[sc.Book]):
        """Split one folder-glued book into several using the album tag."""
        if not book or book.file_count < 2:
            QMessageBox.information(self, "Auto-Split",
                "Select a book with several files first.")
            return
        # Fast scan may not have read every file yet — hydrate now
        for af in book.files:
            if not af.hydrated:
                t = tg.read_tags(af.path); t.pop('_error', None)
                af.tags = t
                af.duration = float(t.get('duration', 0) or 0)
                af.hydrated = True

        groups: dict = {}
        for af in book.files:
            key = (af.tags.get('album') or af.tags.get('title') or '').strip()
            groups.setdefault(key, []).append(af)
        if len(groups) < 2:
            QMessageBox.information(self, "Auto-Split",
                "All files share the same album/title tag — nothing to split by.\n"
                "Select rows in the Files tab and use 'Split selected files' instead.")
            return

        lines = "\n".join(f"  •  {k or '(no tag)'}   ({len(v)} file(s))"
                          for k, v in list(groups.items())[:12])
        if len(groups) > 12: lines += "\n  …"
        ans = QMessageBox.question(self, "Auto-Split by Album Tag",
            f"Split '{book.display_name}' into {len(groups)} books?\n\n{lines}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes: return

        lst = self.books if book in self.books else self.import_books
        group_list = list(groups.items())
        # The original book keeps the first group
        first_key, first_files = group_list[0]
        book.files = first_files
        t0 = first_files[0].tags or {}
        if first_key: book.title = first_key
        book.series_num = t0.get('series_num', '') or book.series_num
        book.cover_art  = t0.get('cover_art') or book.cover_art
        book.modified = True
        for key, files in group_list[1:]:
            lst.append(self._new_book_from(book, files))

        self._populate_both_trees(keep_expanded=True)
        self.book_tree.select_book(book) if book in self.books \
            else self.import_tree.select_book(book)
        self._set_status(
            f"Auto-split into {len(groups)} books ✓ — check them, then Save All Tags.")

    def _build_m4b(self, book: Optional[sc.Book]):
        if not book or not book.files:
            QMessageBox.information(self, "Build M4B", "Select a book with files first.")
            return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            QMessageBox.warning(self, "ffmpeg Not Found",
                "Building an M4B requires ffmpeg.\n\n"
                "Either place ffmpeg.exe next to this app, or install it with:\n"
                "winget install ffmpeg\nthen restart this app.")
            return
        default_name = org.sanitize(book.title or book.display_name) + ".m4b"
        default_path = str(book.files[0].path.parent / default_name)
        out, _ = QFileDialog.getSaveFileName(self, "Save M4B As", default_path,
                                             "M4B Audiobook (*.m4b)")
        if not out: return
        out_path = Path(out)
        if out_path in [af.path for af in book.files]:
            QMessageBox.warning(self, "Invalid Output",
                "The output file can't overwrite one of the source files.")
            return
        self.prog_bar.setRange(0, 100); self.prog_bar.setVisible(True)
        self._m4b_thread = M4bThread(book, out_path, ffmpeg)
        self._m4b_thread.progress.connect(self._on_save_progress)
        self._m4b_thread.finished.connect(lambda p, b=book: self._on_m4b_done(b, p))
        self._m4b_thread.error.connect(self._on_error)
        self._m4b_thread.start()
        self._set_status(f"Building {out_path.name}…")

    def _on_m4b_done(self, book: sc.Book, out_path: str):
        self.prog_bar.setVisible(False)
        # Tag the new file with the book's metadata + cover
        try:
            tg.write_tags(Path(out_path), dict(
                title=book.title, album=book.title,
                author=book.author, artist=book.author,
                narrator=book.narrator, composer=book.narrator,
                series=book.series, series_num=book.series_num,
                year=book.year, publisher=book.publisher,
                genre=book.genre, comment=book.description,
                description=book.description, cover_art=book.cover_art))
        except Exception as e:
            self._set_status(f"M4B built, but tagging failed: {e}")
        QMessageBox.information(self, "M4B Built",
            f"Created:\n{out_path}\n\n"
            f"{book.file_count} file(s) became chapters. "
            "Original files are untouched.\nRescan (F5) to see it in the tree.")
        self._set_status(f"Built {Path(out_path).name} ✓")

    def _delete_empty_folders(self):
        roots = []
        if self.last_folder:
            roots.append(('Library', Path(self.last_folder)))
        if self.import_enabled and self.import_folder:
            roots.append(('Import', Path(self.import_folder)))
        if not roots:
            QMessageBox.information(self, "Delete Empty Folders", "Open a folder first.")
            return
        listing = "\n".join(f"  {name}:  {path}" for name, path in roots)
        ans = QMessageBox.question(self, "Delete Empty Folders",
            "Delete all empty folders — including ones that only contain "
            f"thumbnails / .nfo / playlist junk — inside:\n{listing}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes: return
        removed = 0
        for _, root in roots:
            dirs = sorted((d for d in root.rglob('*') if d.is_dir()),
                          key=lambda p: len(p.parts), reverse=True)
            for d in dirs:
                if not d.exists(): continue   # removed with a parent already
                if self._dir_is_junk_only(d):
                    try:
                        shutil.rmtree(d); removed += 1
                        log_line(f"Removed junk/empty folder: {d}")
                    except OSError:
                        pass
        self._set_status(f"Removed {removed} empty/junk-only folder(s).")

    # ── selection & tab sync ──────────────────────────────────────

    def _on_book_selected(self, book: Optional[sc.Book]):
        self.current_book = book
        if book is None:
            self.book_header.setText("Select a book from the tree")
            self.files_tab.set_book(None)
            self.path_btn.setVisible(False)
        else:
            self.book_header.setText(book.display_name or "Untitled")
            self.files_tab.set_book(book)
            self._update_path_btn(book)

    def _update_path_btn(self, book: sc.Book):
        if not book or not book.files:
            self.path_btn.setVisible(False); return
        folder = book.files[0].path.parent
        full   = str(folder)
        shown  = full if len(full) <= 70 else "…" + full[-68:]
        self.path_btn.setText(f"📁  {shown}")
        self.path_btn.setToolTip(f"{full}\n(click to open in Explorer)")
        self.path_btn.setVisible(True)

    def _open_current_book_folder(self):
        if not self.current_book or not self.current_book.files: return
        folder = self.current_book.files[0].path.parent
        try:
            import os; os.startfile(str(folder))
        except Exception as e:
            self._set_status(f"Couldn't open folder: {e}")

    def _on_selection_changed(self, books: List[sc.Book]):
        self.meta_tab.set_books(books)
        self.move_tab.set_books(books, self.books + self.import_books)
        self.files_tab.set_books(books)

    def _on_files_moved(self):
        self._populate_both_trees(keep_expanded=True)
        if self.current_book:
            self.files_tab.set_book(self.current_book)
            self.book_tree.select_book(self.current_book)
            self.import_tree.select_book(self.current_book)

    def _on_book_created(self, book: sc.Book):
        self.books.append(book)
        self._populate_tree()
        self.book_tree.select_book(book)
        self._set_status(
            f"New book created in '{book.series}' — set its title in the Edit Metadata tab")

    def _on_books_metadata_changed(self):
        self._populate_both_trees(keep_expanded=True)
        if self.current_book:
            self.meta_tab.set_books([self.current_book])
            self.book_tree.select_book(self.current_book)
            self.import_tree.select_book(self.current_book)

    # ── context menu ──────────────────────────────────────────────

    def _book_context_menu(self, pos):
        tree = self.sender() if isinstance(self.sender(), BookTreeWidget) else self.book_tree
        item = tree.itemAt(pos)
        if not item: return
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d: return

        from_import = tree is self.import_tree
        menu = QMenu(self)

        if d[0] == BookTreeWidget.NODE_BOOK:
            book = tree._book_by_id(d[1])
            if not book: return
            sel = tree.selected_books()
            if len(sel) >= 2:
                merge_act = menu.addAction(f"🔗  Merge {len(sel)} selected books into one")
                merge_act.triggered.connect(lambda: self._merge_books(sel))
                menu.addSeparator()
            menu.addAction("Search Metadata…").triggered.connect(
                self.meta_tab._search_internet)
            menu.addAction("🔍 Find duplicates of this book").triggered.connect(
                lambda checked=False, b=book: self._find_duplicates_scoped(b))
            if book.file_count >= 2:
                menu.addAction("✂ Auto-split by album tag").triggered.connect(
                    lambda checked=False, b=book: self._autosplit_book(b))
            menu.addSeparator()
            label_author = "📂 Move to Library / Author Folder" if from_import else "📂 Move to Author Folder"
            label_series = "📖 Move to Library / Series Folder" if from_import else "📖 Move to Series Folder"
            act_author = menu.addAction(label_author)
            act_author.triggered.connect(lambda: self._move_book_to_folder(book, use_series=False))
            act_author.setEnabled(bool(book.author) and bool(self.last_folder))
            act_series = menu.addAction(label_series)
            act_series.triggered.connect(lambda: self._move_book_to_folder(book, use_series=True))
            act_series.setEnabled(bool(book.author and book.series) and bool(self.last_folder))
            menu.addSeparator()
            menu.addAction("Remove from List (no files deleted)").triggered.connect(
                self._remove_selected)
            targets = sel if (len(sel) >= 2 and book in sel) else [book]
            n_files = sum(b.file_count for b in targets)
            del_menu = menu.addMenu(f"🗑 Delete {len(targets)} book(s)…")
            del_act = del_menu.addAction(
                f"Yes — move {n_files} file(s) to the Recycle Bin")
            del_act.triggered.connect(
                lambda checked=False, t=list(targets): self._delete_books_from_disk(t))

        elif d[0] == BookTreeWidget.NODE_AUTHOR:
            books = self._books_under_item(item, tree)
            if not books: return
            rename_act = menu.addAction(f"✏️  Set / rename author for {len(books)} book(s)…")
            rename_act.triggered.connect(lambda: self._rename_author_group(books, d[1]))
            if len(books) >= 2:
                merge_act = menu.addAction(f"🔗  Merge all {len(books)} books into one")
                merge_act.triggered.connect(lambda: self._merge_books(books))
            dup_act = menu.addAction("🔍 Check this author for duplicates (Library + Import)")
            dup_act.triggered.connect(
                lambda checked=False, a=d[1]: self._find_duplicates_author(a))
            rescan_act = menu.addAction("🔄 Rescan this author's folder")
            rescan_act.triggered.connect(
                lambda checked=False, a=d[1], t=tree: self._rescan_author(a, t))
            menu.addSeparator()
            label = (f"📂 Move all {len(books)} book(s) to Library / Author folders"
                     if from_import else
                     f"📂 Move all {len(books)} book(s) to author folders")
            act = menu.addAction(label)
            act.triggered.connect(lambda: self._move_books_batch(books))
            act.setEnabled(bool(self.last_folder))

        elif d[0] == BookTreeWidget.NODE_SERIES:
            books = self._books_under_item(item, tree)
            if not books: return
            if len(books) >= 2:
                merge_act = menu.addAction(f"🔗  Merge all {len(books)} books in this series into one")
                merge_act.triggered.connect(lambda: self._merge_books(books))
                menu.addSeparator()
            label = (f"📖 Move all {len(books)} book(s) to Library / Series folder"
                     if from_import else
                     f"📖 Move all {len(books)} book(s) to series folder")
            act = menu.addAction(label)
            act.triggered.connect(lambda: self._move_books_batch(books))
            act.setEnabled(bool(self.last_folder))

        if menu.actions():
            menu.exec(tree.mapToGlobal(pos))

    def _books_under_item(self, item, tree=None) -> List[sc.Book]:
        """Recursively collect all Book objects that are children of a tree item."""
        if tree is None: tree = self.book_tree
        books = []
        for i in range(item.childCount()):
            child = item.child(i)
            d = child.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == BookTreeWidget.NODE_BOOK:
                book = tree._book_by_id(d[1])
                if book: books.append(book)
            else:
                books.extend(self._books_under_item(child, tree))
        return books

    # Files with only these left behind mean a source folder is safe to delete
    JUNK_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.thumb',
                 '.nfo', '.txt', '.ini', '.db', '.cue', '.m3u', '.m3u8',
                 '.url', '.sfv', '.md5', '.log', '.accurip', '.torrent'}

    def _move_books_batch(self, books: List[sc.Book]):
        self._start_book_move(books, use_series=True)

    def _start_book_move(self, books: List[sc.Book], use_series: bool = True,
                         then_save: bool = True):
        """Move books' files into the Library structure on a worker thread,
        then write their tags. Never blocks the UI."""
        if not self.last_folder:
            QMessageBox.warning(self, "No Folder Open", "Open a library folder first.")
            return
        if self._org_thread and self._org_thread.isRunning():
            self._set_status("A move is already in progress — wait for it to finish.")
            return

        moves = []; afmap = {}; no_author = []
        for book in books:
            if not book.author:
                no_author.append(book.display_name); continue
            series     = book.series     if use_series else ""
            series_num = book.series_num if use_series else ""
            target_dir = org.build_folder_path(
                self.last_folder, book.author, series, series_num,
                book.title or book.display_name)
            total = book.file_count
            for i, af in enumerate(book.files, 1):
                stem = org.build_file_name(book.title or book.display_name, str(i), str(total))
                dst  = target_dir / (stem + af.ext)
                if af.path != dst:
                    moves.append((af.path, dst))
                    afmap[af.path] = af

        if not moves:
            msg = "All books are already in the correct folders."
            if no_author:
                msg += f"\n({len(no_author)} book(s) skipped — no author set)"
            self._set_status(msg)
            # Files may already be in place, but the drop/edit could still have
            # changed tags — flush those so no "unsaved" state lingers
            if then_save:
                dirty = [b for b in books if b.modified]
                if dirty: self._save_books(dirty)
            return

        self._pending_move_ctx = {'books': books, 'afmap': afmap,
                                  'then_save': then_save}
        self.prog_bar.setRange(0, len(moves)); self.prog_bar.setVisible(True)
        # skip_existing=True → collisions stay at the source, never overwritten
        self._org_thread = OrganizeThread(moves, False, True)
        self._org_thread.progress.connect(self._on_save_progress)
        self._org_thread.finished.connect(self._on_book_move_done)
        self._org_thread.error.connect(self._on_error)
        self._org_thread.start()
        self._set_status(f"Moving {len(moves)} file(s)…")

    def _on_book_move_done(self, done, errors, performed, skipped, renamed):
        ctx   = self._pending_move_ctx or {}
        books = ctx.get('books', [])
        afmap = ctx.get('afmap', {})
        self._pending_move_ctx = None
        self.prog_bar.setVisible(False)

        for src, dst in performed:
            af = afmap.get(src)
            if af is not None: af.path = dst

        self._log_op(f"Moved {len(performed)} file(s)", performed, is_copy=False)
        for src, dst in skipped:
            log_line(f"SKIPPED (destination exists): {src} → {dst}")
        if skipped:
            QMessageBox.warning(self, "Some Files Not Moved",
                f"{len(skipped)} file(s) stayed where they are because the "
                "destination already has a file with that name (nothing is "
                "ever overwritten):\n"
                + "\n".join(str(d) for _, d in skipped[:8])
                + "\n\nUse Find Duplicates to resolve.")
        if errors:
            QMessageBox.warning(self, "Some Files Failed", "\n".join(errors[:10]))

        self._cleanup_source_folders([src for src, _ in performed])
        migrated = self._migrate_to_library_if_needed(books)
        msg = f"Moved {len(performed)} file(s) across {len(books)} book(s)."
        if migrated: msg += f"  ({migrated} book(s) → Library)"
        self._set_status(msg)
        self._populate_both_trees(keep_expanded=True)
        if self.current_book in books:
            self.files_tab.set_book(self.current_book)
            self._update_path_btn(self.current_book)
        # Request 2: files get their author/series/etc. tags after landing
        if ctx.get('then_save') and performed and books:
            self._save_books(books)

    def _dir_is_junk_only(self, d: Path) -> bool:
        """True when the folder (recursively) contains no audio and nothing
        but junk files — thumbnails, .nfo, playlists, thumbs.db …"""
        junk_names = {'thumbs.db', 'desktop.ini', '.ds_store'}
        try:
            for e in d.rglob('*'):
                if e.is_file():
                    if e.suffix.lower() in sc.AUDIO_EXTENSIONS: return False
                    if (e.suffix.lower() not in self.JUNK_EXTS
                            and e.name.lower() not in junk_names):
                        return False
        except OSError:
            return False
        return True

    def _cleanup_source_folders(self, src_paths: list):
        """After a move, delete source folders that hold nothing but junk
        (thumbnails, .nfo, playlists …), then any empty parents."""
        roots = set()
        for f in (self.last_folder, self.import_folder):
            if f:
                try: roots.add(Path(f).resolve())
                except OSError: pass

        for d in {Path(p).parent for p in src_paths}:
            cur = d
            while cur.exists():
                try: rcur = cur.resolve()
                except OSError: break
                if rcur in roots: break
                if not any(rcur.is_relative_to(r) for r in roots): break
                if not self._dir_is_junk_only(cur): break
                try:
                    shutil.rmtree(cur)
                    log_line(f"Removed emptied folder: {cur}")
                except OSError:
                    break
                cur = cur.parent

    def _migrate_to_library_if_needed(self, books: List[sc.Book]) -> int:
        """After a physical move, books whose files now live under the library
        root are moved from import_books → library books."""
        if not self.last_folder or not self.import_books:
            return 0
        lib_root = Path(self.last_folder)
        migrated = 0
        for book in list(books):
            if book not in self.import_books: continue
            if not book.files: continue
            try:
                book.files[0].path.relative_to(lib_root)
            except ValueError:
                continue
            self.import_books.remove(book)
            if book not in self.books: self.books.append(book)
            if book in self.import_tree._books:
                self.import_tree._books.remove(book)
            migrated += 1
        return migrated

    def _rescan_author(self, author: str, tree: 'BookTreeWidget'):
        """Rescan only one author's folder in whichever tree was clicked."""
        from_import = tree is self.import_tree
        root_str = self.import_folder if from_import else self.last_folder
        if not root_str:
            self._set_status("No folder configured for this tree."); return
        if author == 'Unknown Author':
            self._set_status("Unknown-author books have no author folder to rescan.")
            return
        folder = Path(root_str) / org.sanitize(author)
        if not folder.exists():
            self._set_status(f"No folder found: {folder}")
            return
        if ((self._scan_thread and self._scan_thread.isRunning())
                or (self._import_scan_thread and self._import_scan_thread.isRunning())
                or (self._author_scan_thread and self._author_scan_thread.isRunning())):
            self._set_status("Wait for the current scan to finish."); return

        lst = self.import_books if from_import else self.books
        for b in list(lst):   # drop books whose files live under that folder
            if not b.files: continue
            try:
                b.files[0].path.relative_to(folder)
            except ValueError:
                continue
            lst.remove(b)
            for tr in (self.book_tree, self.import_tree):
                if b in tr._books: tr._books.remove(b)
            if self.current_book is b:
                self.current_book = None
                self.files_tab.set_book(None)

        self._author_scan_ctx = {'list': lst, 'author': author}
        self._set_status(f"Rescanning {folder}…")
        t = ScanThread(str(folder))
        t.book_ready.connect(self._on_author_book_ready)
        t.finished.connect(self._on_author_rescan_done)
        t.error.connect(self._on_error)
        self._author_scan_thread = t
        t.start()

    def _on_author_book_ready(self, book: sc.Book):
        ctx = self._author_scan_ctx or {}
        # Scanning inside the author folder means path inference would guess
        # the series/title folder as author — force the real one for untagged files
        t0 = book.files[0].tags if book.files else {}
        if not (t0.get('author') or t0.get('artist')):
            book.author = ctx.get('author', book.author)
        ctx.get('list', []).append(book)

    def _on_author_rescan_done(self, _books):
        ctx = self._author_scan_ctx or {}
        lst = ctx.get('list', [])
        lst.sort(key=lambda b: ((b.author or '').lower(), (b.title or '').lower()))
        self._populate_both_trees(keep_expanded=True)
        self._set_status(f"Rescanned '{ctx.get('author', '')}' ✓")
        self._start_hydration([b for b in lst
                               if any(not af.hydrated for af in b.files)])
        self._author_scan_ctx = None

    def _rename_author_group(self, books: List[sc.Book], current_name: str):
        """Prompt for an author name and apply it to every book in the group."""
        default = '' if current_name == 'Unknown Author' else current_name
        new_name, ok = QInputDialog.getText(
            self, "Set / Rename Author",
            f"Author for {len(books)} book(s):",
            QLineEdit.EchoMode.Normal, default)
        if not ok: return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "Empty Name", "Author name can't be empty.")
            return
        for book in books:
            book.author = new_name; book.modified = True
        self._populate_both_trees(keep_expanded=True)
        if self.last_folder:
            for book in books: self._ensure_author_folder(book)
        self._set_status(
            f"Set author = '{new_name}' for {len(books)} book(s) — unsaved. "
            f"Press Ctrl+S or Save All Tags to write.")

    def _move_book_to_folder(self, book: sc.Book, use_series: bool):
        if not book.author:
            QMessageBox.warning(self, "No Author",
                "This book has no author set. Fill in the Author field first.")
            return
        self._start_book_move([book], use_series=use_series)

    def _remove_selected(self):
        for book in self.book_tree.selected_books():
            if book in self.books: self.books.remove(book)
        self._populate_tree()

    def _on_error(self, msg):
        self.prog_bar.setVisible(False); self._set_status(f"Error: {msg}")
        QMessageBox.critical(self, "Error", msg)

    def _set_status(self, msg):
        self.status_bar.showMessage(msg)
        log_line(msg)

    def _open_log(self):
        try:
            if not LOG_PATH.exists():
                LOG_PATH.write_text('', encoding='utf-8')
            import os; os.startfile(str(LOG_PATH))
        except Exception as e:
            self._set_status(f"Couldn't open log: {e}")

    def closeEvent(self, event):
        modified = [b for b in (self.books + self.import_books) if b.modified]
        if modified:
            ans = QMessageBox.question(self, "Unsaved Changes",
                f"You have {len(modified)} unsaved book(s). Exit without saving?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes: event.ignore(); return
        self._stop_hydration()
        if self._dup_thread and self._dup_thread.isRunning():
            self._dup_thread.stop(); self._dup_thread.wait(500)
        self._persist_settings()
        log_line("=== AudioBook Manager v2 closed ===")
        event.accept()
