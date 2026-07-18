#!/usr/bin/env python3
"""AudioBook Manager v2 — PyQt6 GUI
Combines the BookTreeWidget + Open Library import from AudiobookManager
with the tabbed Edit Metadata / Move / Rename interface from old.py.
"""

import sys, json, re, shutil
from pathlib import Path
from typing import Optional, List

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


# ─── Theme ────────────────────────────────────────────────────────────────────

STYLE = """
QMainWindow,QWidget           { background:#1e1e2e; color:#cdd6f4; }
QTreeWidget                   { background:#181825; border:1px solid #313244; border-radius:4px; }
QTreeWidget::item             { padding:3px 4px; }
QTreeWidget::item:selected    { background:#313244; color:#89b4fa; }
QTreeWidget::item:hover       { background:#2a2a3e; }
QLineEdit,QComboBox           { background:#181825; border:1px solid #313244;
                                border-radius:4px; padding:4px 6px; }
QLineEdit:focus               { border-color:#89b4fa; }
QTextEdit                     { background:#181825; border:1px solid #313244;
                                border-radius:4px; padding:4px; }
QPushButton                   { background:#313244; border:1px solid #45475a;
                                border-radius:4px; padding:5px 12px; }
QPushButton:hover             { background:#45475a; }
QPushButton:pressed           { background:#89b4fa; color:#1e1e2e; }
QPushButton:disabled          { color:#585b70; background:#1e1e2e; }
QTableWidget                  { background:#181825; border:1px solid #313244;
                                gridline-color:#2a2a3e; }
QTableWidget::item:selected   { background:#2a3a4a; }
QHeaderView::section          { background:#1e1e2e; color:#a6adc8; border:none;
                                border-bottom:1px solid #313244; padding:4px 8px;
                                font-weight:bold; }
QScrollBar:vertical           { background:#181825; width:8px; border:none; }
QScrollBar::handle:vertical   { background:#45475a; border-radius:3px; }
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical { height:0; }
QSplitter::handle             { background:#313244; }
QToolBar                      { background:#181825; border-bottom:1px solid #313244;
                                spacing:2px; padding:3px; }
QStatusBar                    { background:#181825; color:#a6adc8; }
QGroupBox                     { border:1px solid #313244; border-radius:4px;
                                margin-top:8px; color:#a6adc8; }
QGroupBox::title              { subcontrol-origin:margin; left:8px; padding:0 4px; }
QProgressBar                  { border:1px solid #313244; border-radius:3px;
                                background:#181825; text-align:center; color:#cdd6f4; }
QProgressBar::chunk           { background:#89b4fa; border-radius:2px; }
QDialog                       { background:#1e1e2e; }
QMenu                         { background:#1e1e2e; border:1px solid #313244; }
QMenu::item:selected          { background:#313244; }
QCheckBox::indicator          { width:14px; height:14px; border:1px solid #45475a;
                                border-radius:3px; background:#181825; }
QCheckBox::indicator:checked  { background:#89b4fa; border-color:#89b4fa; }
QTabWidget::pane              { border:1px solid #313244; border-radius:4px; }
QTabBar::tab                  { background:#181825; border:1px solid #313244;
                                padding:6px 14px; margin-right:2px; border-radius:4px 4px 0 0; }
QTabBar::tab:selected         { background:#313244; color:#89b4fa; }
QTabBar::tab:hover            { background:#2a2a3e; }
QSpinBox                      { background:#181825; border:1px solid #313244;
                                border-radius:4px; padding:4px 6px; }
"""

BTN_PRIMARY  = "background:#89b4fa;color:#1e1e2e;font-weight:bold;padding:6px 18px;border-radius:4px;"
BTN_ACCENT   = "background:#f38ba8;color:#1e1e2e;font-weight:bold;padding:5px 12px;border-radius:4px;"
YELLOW       = "#f9e2af"
GREEN        = "#a6e3a1"
BLUE         = "#89b4fa"
GRAY         = "#a6adc8"
LAVENDER     = "#b4befe"
PEACH        = "#fab387"
RED          = "#f38ba8"
ORANGE       = "#fe640b"
CELL_PICKED  = "#1a3a1a"

MIME_FILES      = "application/x-audiobook-files"
MIME_BOOKNODES  = "application/x-audiobook-book-nodes"
MIME_FILEBLOCK  = "application/x-audiobook-file-block"

COL_TITLE, COL_AUTHOR, COL_NARRATOR, COL_SERIES, COL_SNUM, COL_YEAR, COL_PUB = range(7)
COL_NAMES = ["Title", "Author", "Narrator", "Series", "#", "Year", "Publisher"]
COL_KEYS  = ['title', 'author', 'narrator', 'series', 'series_num', 'year', 'publisher']

SETTINGS_PATH = Path.home() / '.audiobookmanagerv2.json'


def _load_settings() -> dict:
    try: return json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
    except Exception: return {}


def _save_settings(d: dict) -> None:
    try: SETTINGS_PATH.write_text(json.dumps(d, indent=2), encoding='utf-8')
    except Exception: pass


LOG_PATH = Path.home() / '.audiobookmanagerv2.log'


def log_line(msg: str) -> None:
    """Append a timestamped line to the session log."""
    try:
        from datetime import datetime
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass


def _rotate_log() -> None:
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 2_000_000:
            old = LOG_PATH.with_name(LOG_PATH.name + '.old')
            if old.exists(): old.unlink()
            LOG_PATH.rename(old)
    except OSError:
        pass


def send_to_recycle_bin(paths: list) -> bool:
    """Move files/folders to the Windows Recycle Bin (no shell confirmation).
    One call handles the whole batch, so it's a single Bin entry to restore."""
    if not paths:
        return True
    import os
    if os.name != 'nt':
        for p in paths:
            try: Path(p).unlink()
            except OSError: return False
        return True
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd",   wintypes.HWND),
                    ("wFunc",  ctypes.c_uint),
                    ("pFrom",  ctypes.c_wchar_p),
                    ("pTo",    ctypes.c_wchar_p),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_wchar_p)]

    FO_DELETE          = 3
    FOF_ALLOWUNDO      = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT         = 0x04
    FOF_NOERRORUI      = 0x400

    op = SHFILEOPSTRUCTW()
    op.wFunc  = FO_DELETE
    op.pFrom  = '\0'.join(str(p) for p in paths) + '\0'   # double-null terminated
    op.pTo    = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return res == 0 and not op.fAnyOperationsAborted


# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_audiobook_title(name: str) -> dict:
    name = name.strip(); out = {}
    m = re.match(r'^(.+?)\s*\(([^)]+?),?\s*#(\d+(?:\.\d+)?)\)\s*$', name)
    if m:
        out['title'] = m.group(1).strip(); out['series'] = m.group(2).strip()
        out['series_num'] = m.group(3); return out
    m = re.match(r'^(.+?)\s*\(([^)]+?),?\s+[Bb]ook\s+(\d+(?:\.\d+)?)\)\s*$', name)
    if m:
        out['title'] = m.group(1).strip(); out['series'] = m.group(2).strip()
        out['series_num'] = m.group(3); return out
    m = re.match(r'^(.+?)\s+(?:#|[Bb]ook\s+)(\d+(?:\.\d+)?)\s*[-:]\s*(.+)$', name)
    if m:
        out['series'] = m.group(1).strip(); out['series_num'] = m.group(2)
        out['title'] = m.group(3).strip(); return out
    out['title'] = name; return out


def _sanitize(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip('. ') or 'Unknown'


def find_ffmpeg() -> Optional[str]:
    """An ffmpeg.exe sitting next to the app (bundled) wins over PATH."""
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).parent   # packaged exe
    else:
        app_dir = Path(__file__).parent          # running from source
    for cand in (app_dir / 'ffmpeg.exe',
                 app_dir / 'dist' / 'ffmpeg.exe'):   # dev runs find the bundled one
        if cand.exists():
            return str(cand)
    return shutil.which('ffmpeg')


# ─── Worker threads ───────────────────────────────────────────────────────────

class ScanThread(QThread):
    progress       = pyqtSignal(int, int, str)
    book_ready     = pyqtSignal(object)   # emitted for each Book as it is found
    problems_found = pyqtSignal(list)     # [(Path, error_str), …]
    finished       = pyqtSignal(list)
    error          = pyqtSignal(str)
    def __init__(self, folder): super().__init__(); self.folder = folder
    def run(self):
        try:
            problems: list = []
            books = sc.scan_folder(self.folder, self._cb, self._book_cb,
                                   problems, fast=True)
            self.problems_found.emit(problems)
            self.finished.emit(books)
        except Exception as e: self.error.emit(str(e))
    def _cb(self, c, t, m): self.progress.emit(c, t, m)
    def _book_cb(self, book): self.book_ready.emit(book)


class HydrateThread(QThread):
    """Background pass that reads tags/durations for the files a fast scan
    skipped. Emits each book as it completes so the UI can update."""
    book_hydrated = pyqtSignal(object)
    def __init__(self, books):
        super().__init__(); self.books = books; self._stop = False
    def stop(self): self._stop = True
    def run(self):
        for book in list(self.books):
            if self._stop: return
            changed = False
            for af in book.files:
                if self._stop: return
                if af.hydrated: continue
                tags = tg.read_tags(af.path)
                tags.pop('_error', None)
                af.tags = tags
                af.duration = float(tags.get('duration', 0) or 0)
                af.hydrated = True
                changed = True
            if changed:
                self.book_hydrated.emit(book)


class SaveThread(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal()
    error    = pyqtSignal(str)
    def __init__(self, books): super().__init__(); self.books = books
    def run(self):
        try:
            total = sum(b.file_count for b in self.books); done = 0
            for book in self.books:
                fields = dict(
                    title=book.title, album=book.title,
                    author=book.author, artist=book.author,
                    narrator=book.narrator, composer=book.narrator,
                    series=book.series, series_num=book.series_num,
                    year=book.year, publisher=book.publisher,
                    genre=book.genre, comment=book.description,
                    description=book.description,
                    track_total=str(book.file_count))
                for i, af in enumerate(book.files, 1):
                    self.progress.emit(done, total, f"Saving {af.filename}…")
                    fields['track'] = str(i)
                    # Fast scans may not have read this file yet — hydrate now
                    # so its own cover isn't lost.
                    if not af.hydrated:
                        t = tg.read_tags(af.path); t.pop('_error', None)
                        af.tags = t
                        af.duration = float(t.get('duration', 0) or 0)
                        af.hydrated = True
                    # Explicit cover overrides everything; otherwise each file
                    # keeps its own art (merged episodes stay distinct), with
                    # the book cover only filling in files that have none.
                    if book.cover_explicit:
                        cover = book.cover_art
                    else:
                        cover = af.tags.get('cover_art') or book.cover_art
                    fields['cover_art'] = cover
                    if cover:
                        af.tags['cover_art'] = cover
                    tg.write_tags(af.path, fields); done += 1
            self.finished.emit()
        except Exception as e: self.error.emit(str(e))


class DupCheckThread(QThread):
    """Compare books that share a title: file count → sizes → MD5 hashes."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)   # report lines

    def __init__(self, books):
        super().__init__(); self.books = books; self._stop = False

    def stop(self): self._stop = True

    def run(self):
        import hashlib
        groups: dict = {}
        for b in self.books:
            t = (b.title or '').strip().lower()
            a = (b.author or '').strip().lower()
            # Same title by DIFFERENT authors is legitimate — only compare
            # books that share both author and title
            if t and b.files: groups.setdefault((a, t), []).append(b)
        report = []
        for key, bs in sorted(groups.items()):
            if len(bs) < 2: continue
            for i in range(len(bs)):
                for j in range(i + 1, len(bs)):
                    if self._stop: return
                    a, c = bs[i], bs[j]
                    self.progress.emit(f"Comparing '{a.display_name}'…")
                    report.append((self._compare(a, c), a, c))
        self.finished.emit(report)

    def _compare(self, a, b) -> str:
        if a.file_count != b.file_count:
            return "⚠ SAME TITLE, different file count"
        try:
            sa = sorted(af.path.stat().st_size for af in a.files)
            sb = sorted(af.path.stat().st_size for af in b.files)
        except OSError:
            return "⚠ SAME TITLE (couldn't read sizes)"

        if sa == sb:
            ha, hb = self._book_hash(a, full=True), self._book_hash(b, full=True)
            if ha is None or hb is None: return "cancelled"
            if ha == hb:
                return "🔴 EXACT DUPLICATE (byte-identical files)"

        # Bytes differ — often just tags/covers. Compare only the audio
        # streams (ID3/atom/metadata containers skipped).
        ha, hb = self._book_hash(a, full=False), self._book_hash(b, full=False)
        if ha is None or hb is None: return "cancelled"
        if ha == hb:
            return "🟠 SAME AUDIO — only the tags/covers differ"
        return ("⚠ SAME TITLE + sizes, but different audio" if sa == sb
                else "⚠ SAME TITLE, different audio")

    def _book_hash(self, book, full: bool):
        """Sorted per-file hashes: full-file MD5 or tag-agnostic audio MD5."""
        import hashlib
        hashes = []
        for af in book.files:
            if self._stop: return None
            if full:
                h = hashlib.md5()
                try:
                    with open(af.path, 'rb') as f:
                        for chunk in iter(lambda: f.read(1 << 20), b''):
                            if self._stop: return None
                            h.update(chunk)
                    hashes.append(h.hexdigest())
                except OSError:
                    hashes.append(f"ERR:{af.path}")   # unique → never matches
            else:
                d = tg.audio_content_md5(af.path)
                hashes.append(d if d else f"ERR:{af.path}")
        return sorted(hashes)


class FieldSaveThread(QThread):
    """Write (or, when value is empty, DELETE) one field on every file of
    the given books — touches nothing else, not even covers."""
    finished = pyqtSignal(int)
    error    = pyqtSignal(str)

    ALIASES = {
        'title':    ('title', 'album'),
        'author':   ('author', 'artist'),
        'narrator': ('narrator', 'composer'),
    }

    def __init__(self, books, field_key: str, value: str):
        super().__init__()
        self.books = books; self.field_key = field_key; self.value = value

    def run(self):
        try:
            n = 0
            if self.value == '':
                for book in self.books:
                    for af in book.files:
                        tg.delete_field(af.path, self.field_key)
                        n += 1
            else:
                keys = self.ALIASES.get(self.field_key, (self.field_key,))
                fields = {k: self.value for k in keys}
                for book in self.books:
                    for af in book.files:
                        tg.write_tags(af.path, fields)
                        n += 1
            self.finished.emit(n)
        except Exception as e:
            self.error.emit(str(e))


class SearchThread(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)
    def __init__(self, query, source: str = 'Audible'):
        super().__init__(); self.query = query; self.source = source
    def run(self):
        try:
            fn = au.search_books if self.source == 'Audible' else ol.search_books
            self.finished.emit(fn(self.query))
        except Exception as e: self.error.emit(str(e))


class OrganizeThread(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, list, list, list, list)  # done, errors, performed, skipped, renamed
    error    = pyqtSignal(str)
    def __init__(self, moves, copy_mode, skip_existing):
        super().__init__(); self.moves=moves; self.copy=copy_mode; self.skip=skip_existing
    def run(self):
        try:
            done, errs, performed, skipped, renamed = org.apply_moves(self.moves,
                copy=self.copy, skip_existing=self.skip, progress_cb=self._cb)
            self.finished.emit(done, errs, performed, skipped, renamed)
        except Exception as e: self.error.emit(str(e))
    def _cb(self, c, t, m): self.progress.emit(c, t, m)


def _ffesc(val: str) -> str:
    """Escape a value for an ffmetadata file."""
    out = str(val or '')
    for ch in ('\\', '=', ';', '#'):
        out = out.replace(ch, '\\' + ch)
    return out.replace('\n', '\\\n')


class M4bThread(QThread):
    """Concatenate a book's files into one .m4b with chapters via ffmpeg."""
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(str)              # output path
    error    = pyqtSignal(str)

    def __init__(self, book, out_path: Path, ffmpeg: str):
        super().__init__()
        self.book = book; self.out_path = out_path; self.ffmpeg = ffmpeg

    def run(self):
        import subprocess, tempfile, os
        try:
            total = int(sum(af.duration for af in self.book.files)) or 0
            with tempfile.TemporaryDirectory() as td:
                list_path = Path(td) / 'files.txt'
                meta_path = Path(td) / 'meta.txt'

                with open(list_path, 'w', encoding='utf-8') as f:
                    for af in self.book.files:
                        esc = str(af.path).replace("'", "'\\''")
                        f.write(f"file '{esc}'\n")

                lines = [';FFMETADATA1']
                if self.book.title:
                    lines.append(f'title={_ffesc(self.book.title)}')
                    lines.append(f'album={_ffesc(self.book.title)}')
                if self.book.author:
                    lines.append(f'artist={_ffesc(self.book.author)}')
                t0 = 0.0
                for af in self.book.files:
                    t1 = t0 + max(af.duration, 0)
                    lines += ['[CHAPTER]', 'TIMEBASE=1/1000',
                              f'START={int(t0 * 1000)}', f'END={int(t1 * 1000)}',
                              f'title={_ffesc(af.path.stem)}']
                    t0 = t1
                meta_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

                copy_ok = all(af.ext in ('.m4a', '.m4b', '.aac', '.mp4')
                              for af in self.book.files)
                codec = ['-c:a', 'copy'] if copy_ok else ['-c:a', 'aac', '-b:a', '96k']
                cmd = [self.ffmpeg, '-y', '-f', 'concat', '-safe', '0',
                       '-i', str(list_path), '-i', str(meta_path),
                       '-map_metadata', '1', '-vn', *codec,
                       '-movflags', '+faststart', str(self.out_path)]

                flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, text=True,
                                        encoding='utf-8', errors='replace',
                                        creationflags=flags)
                tail = []
                for line in proc.stderr:
                    tail.append(line.strip()); tail = tail[-12:]
                    m = re.search(r'time=(\d+):(\d+):(\d+(?:\.\d+)?)', line)
                    if m and total:
                        cur = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
                        pct = min(int(cur / total * 100), 100)
                        self.progress.emit(int(cur), total, f"Building M4B…  {pct}%")
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError("ffmpeg failed:\n" + "\n".join(tail[-6:]))
            self.finished.emit(str(self.out_path))
        except Exception as e:
            self.error.emit(str(e))


class RepairThread(QThread):
    """Losslessly remux a damaged file with ffmpeg (-c copy). On success the
    original goes to the Recycle Bin and the repaired file takes its name."""
    done = pyqtSignal(object, bool, str)   # path, ok, message

    def __init__(self, path: Path, ffmpeg: str):
        super().__init__(); self.path = path; self.ffmpeg = ffmpeg

    def run(self):
        import subprocess, os
        src = self.path
        tmp = src.with_name(src.stem + '.repair-tmp' + src.suffix)
        try:
            if src.suffix.lower() in ('.m4b', '.m4a', '.mp4'):
                # Audio + cover only; chapter TEXT tracks break the ipod muxer,
                # but -map_chapters carries the chapters over as metadata
                cmd = [self.ffmpeg, '-y', '-i', str(src),
                       '-map', '0:a', '-map', '0:v?', '-map_chapters', '0',
                       '-c', 'copy', '-movflags', '+faststart', str(tmp)]
            else:
                cmd = [self.ffmpeg, '-y', '-i', str(src),
                       '-map', '0', '-c', 'copy', str(tmp)]
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding='utf-8', errors='replace',
                                  creationflags=flags, timeout=1800)
            if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
                tail = (proc.stderr or '').strip().splitlines()[-3:]
                raise RuntimeError("; ".join(tail) or f"ffmpeg exit {proc.returncode}")
            check = tg.read_tags(tmp)
            if '_error' in check:
                raise RuntimeError(f"repaired file still unreadable: {check['_error']}")
            if not send_to_recycle_bin([src]) or src.exists():
                raise RuntimeError("couldn't move the original to the Recycle Bin")
            tmp.rename(src)
            self.done.emit(src, True, "Repaired — original is in the Recycle Bin. Rescan (F5).")
        except Exception as e:
            try:
                if tmp.exists(): tmp.unlink()
            except OSError:
                pass
            self.done.emit(src, False, str(e))


class CoverFetchThread(QThread):
    cover_ready = pyqtSignal(int, bytes)
    def __init__(self, results):
        super().__init__(); self._results = results; self._stop = False
    def stop(self): self._stop = True
    def run(self):
        for i, r in enumerate(self._results):
            if self._stop: return
            cid, curl = r.get('cover_id'), r.get('cover_url')
            data = None
            if cid:    data = ol.fetch_cover(cid)
            elif curl: data = au.fetch_cover(curl)
            if data and not self._stop:
                self.cover_ready.emit(i, data)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _book_misplacement(book: sc.Book) -> str:
    """
    '' when properly placed, 'author' when the files aren't under a folder
    matching the author, 'series' when they're under the author folder but
    the book has a series tag and no folder level matches it.
    """
    if not book.author or not book.files:
        return ''
    author_norm = org.sanitize(book.author).lower()
    if not author_norm or author_norm == 'unknown':
        return ''

    def folder_parts(af):
        return [org.sanitize(p).lower() for p in af.path.parts[:-1]]

    if not any(author_norm in folder_parts(af) for af in book.files):
        return 'author'

    series = (book.series or '').strip()
    if series:
        series_norm = org.sanitize(series).lower()
        if series_norm and not any(series_norm in folder_parts(af)
                                   for af in book.files):
            return 'series'
    return ''


# ─── Book tree ────────────────────────────────────────────────────────────────

class BookTreeWidget(QTreeWidget):
    book_selected          = pyqtSignal(object)
    selection_changed      = pyqtSignal(list)   # all selected Book objects
    files_moved            = pyqtSignal()
    books_metadata_changed = pyqtSignal()
    book_created           = pyqtSignal(object) # new sc.Book dropped onto a series node
    books_imported         = pyqtSignal(list)   # book ids that just migrated INTO this tree
    books_organize_requested = pyqtSignal(list) # book ids dropped on author/series nodes

    NODE_AUTHOR = "author"
    NODE_SERIES = "series"
    NODE_BOOK   = "book"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._books: List[sc.Book] = []
        self._filter = ""
        self.book_lookup = None      # callable: bid -> sc.Book or None (cross-tree lookup)
        self.external_titles: set = set()   # lowercase titles living in the OTHER tree
        self._dup_titles: set = set()
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDropIndicatorShown(True)
        self.itemSelectionChanged.connect(self._on_selection_changed)

    def populate(self, books: List[sc.Book], keep_expanded: bool = False):
        expanded = self._collect_expanded() if keep_expanded else set()
        self._books = books
        counts: dict = {}
        for b in books:
            key = ((b.author or '').strip().lower(),
                   (b.title or b.display_name or '').strip().lower())
            if key[1]: counts[key] = counts.get(key, 0) + 1
        self._dup_titles = {k for k, c in counts.items() if c > 1}
        self.blockSignals(True)
        self.clear()
        by_author: dict = {}
        for book in books:
            auth = (book.author or "").strip() or "Unknown Author"
            by_author.setdefault(auth, []).append(book)
        for auth in sorted(by_author, key=str.casefold):
            anode = QTreeWidgetItem(self)
            anode.setText(0, f"📚  {auth}")
            anode.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_AUTHOR, auth))
            anode.setFlags((anode.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                           | Qt.ItemFlag.ItemIsDropEnabled)
            anode.setForeground(0, QColor(LAVENDER))
            f = anode.font(0); f.setBold(True); anode.setFont(0, f)
            by_series: dict = {}
            for book in by_author[auth]:
                series = (book.series or "").strip()
                by_series.setdefault(series, []).append(book)
            for series in sorted(by_series, key=str.casefold):
                if series:
                    snode = QTreeWidgetItem(anode)
                    snode.setText(0, f"📖  {series}")
                    snode.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_SERIES, series))
                    snode.setFlags((snode.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                                   | Qt.ItemFlag.ItemIsDropEnabled)
                    snode.setForeground(0, QColor(PEACH))
                    parent_node = snode
                else:
                    parent_node = anode
                def sort_key(b):
                    try:    return (float(b.series_num or 0), (b.title or "").lower())
                    except: return (999.0, (b.title or "").lower())
                for book in sorted(by_series[series], key=sort_key):
                    self._add_book_node(parent_node, book)
        self.blockSignals(False)
        self.expandAll()
        if keep_expanded: self._restore_expanded(expanded)
        self._apply_filter()

    def _add_book_node(self, parent, book):
        node = QTreeWidgetItem(parent)
        self._refresh_node(node, book); return node

    def add_book(self, book: sc.Book):
        """Insert one book into the live tree without a full rebuild."""
        if book not in self._books:
            self._books.append(book)
        auth   = (book.author or "").strip() or "Unknown Author"
        series = (book.series  or "").strip()

        # Find or create the author node
        anode = self._find_node(self.invisibleRootItem(), self.NODE_AUTHOR, auth)
        if anode is None:
            anode = QTreeWidgetItem(self)
            anode.setText(0, f"📚  {auth}")
            anode.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_AUTHOR, auth))
            anode.setFlags((anode.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                           | Qt.ItemFlag.ItemIsDropEnabled)
            anode.setForeground(0, QColor(LAVENDER))
            f = anode.font(0); f.setBold(True); anode.setFont(0, f)

        # Find or create the series node (if the book has a series)
        if series:
            snode = self._find_node(anode, self.NODE_SERIES, series)
            if snode is None:
                snode = QTreeWidgetItem(anode)
                snode.setText(0, f"📖  {series}")
                snode.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_SERIES, series))
                snode.setFlags((snode.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                               | Qt.ItemFlag.ItemIsDropEnabled)
                snode.setForeground(0, QColor(PEACH))
            parent_node = snode
        else:
            parent_node = anode

        self._add_book_node(parent_node, book)
        anode.setExpanded(True)
        if series: parent_node.setExpanded(True)

        # Hide the new node if it doesn't match the active filter
        tl = self._filter
        if tl:
            self._apply_filter()

    def _find_node(self, parent: QTreeWidgetItem, node_type: str, value: str):
        """Return the first direct child of *parent* matching (node_type, value), or None."""
        for i in range(parent.childCount()):
            child = parent.child(i)
            d = child.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == node_type and d[1] == value:
                return child
        return None

    def _refresh_node(self, node, book):
        pos       = f" #{book.series_num}" if book.series_num else ""
        dur       = f"  [{book.duration_str()}]" if book.duration_str() else ""
        files     = f"  ({book.file_count})" if book.file_count else ""
        why       = _book_misplacement(book)
        icon      = "📂" if why else "🎧"
        node.setText(0, f"{icon}  {book.display_name}{pos}{files}{dur}")
        node.setData(0, Qt.ItemDataRole.UserRole, (self.NODE_BOOK, book.id))
        dup_key = ((book.author or '').strip().lower(),
                   (book.title or book.display_name or '').strip().lower())
        dup = bool(dup_key[1]) and (dup_key in self._dup_titles
                                    or dup_key in self.external_titles)
        tip = f"{book.file_count} file(s)  |  {book.author}"
        if why == 'author':
            tip += "\n⚠ Files are not inside the author folder"
        elif why == 'series':
            tip += ("\n⚠ In the author folder but NOT in the series folder — "
                    "right-click → Move to Series Folder")
        if dup:
            tip += "\n⚠ This author has another book with this title (use Find Duplicates)"
        node.setToolTip(0, tip)
        if book.modified:
            color = YELLOW
        elif dup:
            color = RED
        elif why == 'author':
            color = ORANGE
        elif why == 'series':
            color = PEACH
        else:
            color = "#cdd6f4"
        node.setForeground(0, QColor(color))
        node.setFlags(node.flags()
                      | Qt.ItemFlag.ItemIsDragEnabled
                      | Qt.ItemFlag.ItemIsDropEnabled
                      | Qt.ItemFlag.ItemIsSelectable)

    def refresh_book(self, book):
        node = self._find_book_node(book.id)
        if node: self._refresh_node(node, book)

    def _find_book_node(self, bid):
        return self._dfs(self.invisibleRootItem(), bid)

    def _dfs(self, parent, bid):
        for i in range(parent.childCount()):
            child = parent.child(i)
            d = child.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == self.NODE_BOOK and d[1] == bid: return child
            found = self._dfs(child, bid)
            if found: return found
        return None

    def apply_filter(self, text):
        self._filter = text.lower(); self._apply_filter()

    def _apply_filter(self):
        tl = self._filter
        root = self.invisibleRootItem()
        for ai in range(root.childCount()):
            anode = root.child(ai); av = False
            for si in range(anode.childCount()):
                child = anode.child(si)
                d = child.data(0, Qt.ItemDataRole.UserRole)
                if d and d[0] == self.NODE_BOOK:
                    vis = not tl or tl in child.text(0).lower()
                    child.setHidden(not vis)
                    if vis: av = True
                else:
                    sv = False
                    for bi in range(child.childCount()):
                        bnode = child.child(bi)
                        vis = not tl or tl in bnode.text(0).lower()
                        bnode.setHidden(not vis)
                        if vis: sv = True
                    child.setHidden(not sv)
                    if sv: av = True
            anode.setHidden(not av and bool(tl))

    def _on_selection_changed(self):
        sel = self.selectedItems()
        books = []
        primary = None
        for item in sel:
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == self.NODE_BOOK:
                b = self._book_by_id(d[1])
                if b:
                    books.append(b)
                    if primary is None: primary = b
        self.book_selected.emit(primary)
        self.selection_changed.emit(books)

    def selected_books(self) -> List[sc.Book]:
        out = []
        for item in self.selectedItems():
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == self.NODE_BOOK:
                b = self._book_by_id(d[1])
                if b: out.append(b)
        return out

    def select_book(self, book):
        node = self._find_book_node(book.id)
        if node: self.clearSelection(); node.setSelected(True); self.scrollToItem(node)

    def _book_by_id(self, bid):
        return next((b for b in self._books if b.id == bid), None)

    def _resolve_book(self, bid):
        """Find a book by id in this tree, or via the cross-tree lookup."""
        b = self._book_by_id(bid)
        if b is not None: return b
        if self.book_lookup is not None:
            return self.book_lookup(bid)
        return None

    def _collect_expanded(self):
        keys = set(); root = self.invisibleRootItem()
        for ai in range(root.childCount()):
            an = root.child(ai)
            if an.isExpanded(): keys.add(an.text(0))
            for si in range(an.childCount()):
                sn = an.child(si)
                if sn.isExpanded(): keys.add(sn.text(0))
        return keys

    def _restore_expanded(self, keys):
        root = self.invisibleRootItem()
        for ai in range(root.childCount()):
            an = root.child(ai); an.setExpanded(an.text(0) in keys)
            for si in range(an.childCount()):
                sn = an.child(si); sn.setExpanded(sn.text(0) in keys)

    def startDrag(self, supported_actions):
        items = self.selectedItems()
        book_ids = [item.data(0, Qt.ItemDataRole.UserRole)[1]
                    for item in items
                    if item.data(0, Qt.ItemDataRole.UserRole) and
                    item.data(0, Qt.ItemDataRole.UserRole)[0] == self.NODE_BOOK]
        if not book_ids: super().startDrag(supported_actions); return
        mime = QMimeData()
        mime.setData(MIME_BOOKNODES, QByteArray(json.dumps(book_ids).encode()))
        drag = QDrag(self); drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if (event.mimeData().hasFormat(MIME_FILES) or
                event.mimeData().hasFormat(MIME_BOOKNODES)):
            event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        item = self.itemAt(event.position().toPoint())
        if not item:
            # Empty area — allow cross-tree book migration that keeps existing author/series
            if mime.hasFormat(MIME_BOOKNODES):
                event.acceptProposedAction(); return
            event.ignore(); return
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d: event.ignore(); return
        if mime.hasFormat(MIME_FILES) and d[0] in (self.NODE_BOOK, self.NODE_SERIES):
            self.setCurrentItem(item); event.acceptProposedAction()
        elif mime.hasFormat(MIME_BOOKNODES) and d[0] in (self.NODE_SERIES, self.NODE_AUTHOR):
            self.setCurrentItem(item); event.acceptProposedAction()
        else: event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        item = self.itemAt(event.position().toPoint())
        if not item:
            # Cross-tree migration onto empty tree area — keep the book's existing
            # author/series untouched; just move it into this tree.
            if mime.hasFormat(MIME_BOOKNODES) and self.book_lookup is not None:
                book_ids = json.loads(bytes(mime.data(MIME_BOOKNODES)).decode())
                imported_ids = []
                for bid in book_ids:
                    if self._book_by_id(bid) is not None: continue  # already mine
                    book = self.book_lookup(bid)
                    if book is None: continue
                    book.modified = True
                    if book not in self._books: self._books.append(book)
                    imported_ids.append(bid)
                if imported_ids:
                    event.acceptProposedAction()
                    self.books_metadata_changed.emit()
                    self.books_imported.emit(imported_ids)
                    return
            event.ignore(); return
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d: event.ignore(); return
        if mime.hasFormat(MIME_FILES) and d[0] == self.NODE_BOOK:
            target = self._book_by_id(d[1])
            if not target: event.ignore(); return
            payload = json.loads(bytes(mime.data(MIME_FILES)).decode())
            per_src: dict = {}
            for entry in payload:
                per_src.setdefault(entry["src_book_id"], []).append(entry["file_index"])
            moved = False
            for src_id, indices in per_src.items():
                src = self._resolve_book(src_id)
                if not src or src is target: continue
                to_move = []
                for idx in sorted(set(indices), reverse=True):
                    if 0 <= idx < len(src.files):
                        to_move.insert(0, src.files.pop(idx))
                target.files.extend(to_move)
                src.modified = target.modified = True; moved = True
            if moved: event.acceptProposedAction(); self.files_moved.emit()
            else: event.ignore(); return
        if mime.hasFormat(MIME_FILES) and d[0] == self.NODE_SERIES:
            # Drop files onto a series node → spin them off as a new book in that series
            series = d[1]
            author = ""
            parent = item.parent()
            if parent:
                pd = parent.data(0, Qt.ItemDataRole.UserRole)
                if pd and pd[0] == self.NODE_AUTHOR:
                    author = pd[1]
            payload = json.loads(bytes(mime.data(MIME_FILES)).decode())
            per_src: dict = {}
            for entry in payload:
                per_src.setdefault(entry["src_book_id"], []).append(entry["file_index"])
            collected = []
            for src_id, indices in per_src.items():
                src = self._resolve_book(src_id)
                if not src: continue
                for idx in sorted(set(indices), reverse=True):
                    if 0 <= idx < len(src.files):
                        collected.insert(0, src.files.pop(idx))
                src.modified = True
            if not collected: event.ignore(); return
            new_book = sc.Book()
            new_book.files  = sorted(collected, key=lambda af: af.path.name)
            new_book.series = series
            new_book.author = author
            new_book.title  = collected[0].tags.get('album', '') or collected[0].path.stem
            new_book.modified = True
            self._books.append(new_book)
            event.acceptProposedAction()
            self.book_created.emit(new_book)
            return
        if mime.hasFormat(MIME_BOOKNODES) and d[0] in (self.NODE_SERIES, self.NODE_AUTHOR):
            book_ids = json.loads(bytes(mime.data(MIME_BOOKNODES)).decode())
            changed = False
            imported_ids: list = []
            for bid in book_ids:
                book = self._book_by_id(bid)
                is_external = False
                if book is None and self.book_lookup is not None:
                    book = self.book_lookup(bid)
                    is_external = book is not None
                if not book: continue
                before = (book.author, book.series)
                if d[0] == self.NODE_SERIES:
                    book.series = d[1]
                    parent = item.parent()
                    if parent:
                        pd = parent.data(0, Qt.ItemDataRole.UserRole)
                        if pd and pd[0] == self.NODE_AUTHOR:
                            book.author = pd[1]
                elif d[0] == self.NODE_AUTHOR:
                    book.author = d[1]; book.series = ""
                # Only flag unsaved when the drop really changed something —
                # the organize signal still fires either way so files get filed
                if (book.author, book.series) != before:
                    book.modified = True
                changed = True
                if is_external:
                    if book not in self._books:
                        self._books.append(book)
                    imported_ids.append(bid)
            if changed:
                event.acceptProposedAction()
                self.books_metadata_changed.emit()
                if imported_ids:
                    self.books_imported.emit(imported_ids)
                self.books_organize_requested.emit(book_ids)
            else: event.ignore()


# ─── Open Library dialog ──────────────────────────────────────────────────────

class _ClickableLabel(QLabel):
    clicked        = pyqtSignal()
    double_clicked = pyqtSignal()
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(); super().mouseDoubleClickEvent(event)


class CoverViewDialog(QDialog):
    """Full-size cover viewer — click anywhere (inside or outside) to close."""
    def __init__(self, data: bytes, parent=None):
        super().__init__(parent)
        # Popup windows close automatically when you click outside them
        self.setWindowFlags(Qt.WindowType.Popup)
        px = QPixmap(); px.loadFromData(data)
        info_txt = f"{px.width()} × {px.height()} px,  {len(data)//1024} KB   —   click to close"
        scr = QApplication.primaryScreen().availableGeometry()
        maxw, maxh = int(scr.width() * 0.8), int(scr.height() * 0.8)
        if px.width() > maxw or px.height() > maxh:
            px = px.scaled(maxw, maxh, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        lay = QVBoxLayout(self)
        lbl = QLabel(); lbl.setPixmap(px)
        lay.addWidget(lbl)
        info = QLabel(info_txt)
        info.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(info)
        self.adjustSize()
        self.move(scr.center() - self.rect().center())

    def mousePressEvent(self, event):
        self.accept()


class OpenLibraryDialog(QDialog):
    def __init__(self, book: sc.Book, parent=None):
        super().__init__(parent)
        self.book = book
        self._results: list = []
        self._covers:  dict = {}
        self._picks:   dict = {}
        self._cover_pick: Optional[bytes] = None
        self._search_thread: Optional[SearchThread]     = None
        self._cover_thread:  Optional[CoverFetchThread] = None
        self.setWindowTitle("Search Metadata")
        self.setMinimumSize(1050, 600)
        self._build_ui()
        if self.q_edit.text().strip():
            QTimer.singleShot(0, self._search)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        srow = QHBoxLayout()
        self.src_cb = QComboBox()
        self.src_cb.addItems(["Audible", "Open Library"])
        self.src_cb.setToolTip("Audible has narrator + series data; Open Library covers more print books")
        srow.addWidget(self.src_cb)
        self.q_edit = QLineEdit()
        self.q_edit.setPlaceholderText("Search title, author, ISBN …")
        self.q_edit.setText(f"{self.book.title} {self.book.author}".strip())
        self.q_edit.returnPressed.connect(self._search)
        srow.addWidget(self.q_edit)
        self.search_btn = QPushButton("Search")
        self.search_btn.setStyleSheet(BTN_PRIMARY)
        self.search_btn.clicked.connect(self._search)
        srow.addWidget(self.search_btn)
        lay.addLayout(srow)
        self.status_lbl = QLabel("Searching…  •  Double-click any cell to pick that field  •  Double-click the cover to pick the image")
        self.status_lbl.setStyleSheet(f"color:{GRAY}; font-style:italic;")
        lay.addWidget(self.status_lbl)
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        self._filling = False
        self.tbl = QTableWidget(0, len(COL_NAMES) + 1)
        self.tbl.setHorizontalHeaderLabels(["Use"] + COL_NAMES)
        hh = self.tbl.horizontalHeader()
        # Interactive = user-draggable column widths; last column fills the rest
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        for col, wpx in enumerate([40, 280, 150, 140, 140, 40, 60]):
            self.tbl.setColumnWidth(col, wpx)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setWordWrap(True)
        vh = self.tbl.verticalHeader()
        vh.setVisible(True)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        vh.setDefaultSectionSize(24)
        self.tbl.cellClicked.connect(self._on_cell_clicked)
        self.tbl.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.tbl.itemChanged.connect(self._on_use_toggled)
        hsplit.addWidget(self.tbl)
        cover_w = QWidget(); cover_w.setFixedWidth(180)
        cl = QVBoxLayout(cover_w); cl.setContentsMargins(4, 0, 0, 0)
        cl.addWidget(QLabel("Cover Preview"))
        self.cover_preview = _ClickableLabel("No Cover")
        self.cover_preview.setFixedSize(165, 210)
        self.cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_preview.setStyleSheet(f"border:2px solid #313244; border-radius:4px; color:{GRAY};")
        self.cover_preview.double_clicked.connect(self._pick_cover)
        cl.addWidget(self.cover_preview)
        self.cover_status = QLabel("— no cover —")
        self.cover_status.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        self.cover_status.setWordWrap(True)
        cl.addWidget(self.cover_status)
        pick_btn = QPushButton("✔ Pick This Cover")
        pick_btn.clicked.connect(self._pick_cover); cl.addWidget(pick_btn)
        cl.addStretch(); hsplit.addWidget(cover_w); hsplit.setSizes([700, 180])
        lay.addWidget(hsplit)
        self.picks_lbl = QLabel("No fields picked yet — double-click cells above")
        self.picks_lbl.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        lay.addWidget(self.picks_lbl)
        br = QHBoxLayout()
        clear_btn = QPushButton("Clear All Picks"); clear_btn.clicked.connect(self._clear_picks)
        br.addWidget(clear_btn); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        apply  = QPushButton("Apply Picked Fields"); apply.setStyleSheet(BTN_PRIMARY)
        apply.clicked.connect(self.accept)
        br.addWidget(cancel); br.addWidget(apply); lay.addLayout(br)

    def _search(self):
        q = self.q_edit.text().strip()
        if not q: return
        self._stop_cover_thread()
        self.search_btn.setEnabled(False)
        self.status_lbl.setText("Searching…")
        self.tbl.setRowCount(0)
        self._results = []; self._covers = {}; self._picks = {}; self._cover_pick = None
        self._update_cover_preview(None); self._update_picks_label()
        self._search_thread = SearchThread(q, self.src_cb.currentText())
        self._search_thread.finished.connect(self._on_results)
        self._search_thread.error.connect(self._on_search_error)
        self._search_thread.start()

    def _on_results(self, results):
        self._results = results; self.search_btn.setEnabled(True)
        if not results: self.status_lbl.setText("No results found."); return
        self.status_lbl.setText(
            f"{len(results)} result(s)  •  Double-click a cell to pick it  •  "
            "tick 'Use' to pick the whole row")
        self._filling = True
        self.tbl.setRowCount(0)
        for r in results:
            row = self.tbl.rowCount(); self.tbl.insertRow(row)
            use = QTableWidgetItem("")
            use.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                         | Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(Qt.CheckState.Unchecked)
            use.setToolTip("Pick every field of this row (and its cover)")
            self.tbl.setItem(row, 0, use)
            for col, key in enumerate(COL_KEYS):
                self.tbl.setItem(row, col + 1, QTableWidgetItem(str(r.get(key,'') or '')))
        self._filling = False
        self.tbl.selectRow(0); self._on_cell_clicked(0, 0)
        self._cover_thread = CoverFetchThread(results)
        self._cover_thread.cover_ready.connect(self._on_cover_ready)
        self._cover_thread.start()

    def _on_search_error(self, msg):
        self.search_btn.setEnabled(True); self.status_lbl.setText(f"Error: {msg}")

    def _stop_cover_thread(self):
        if self._cover_thread and self._cover_thread.isRunning():
            self._cover_thread.stop(); self._cover_thread.wait(500)

    def _on_cover_ready(self, row, data):
        self._covers[row] = data
        if self.tbl.currentRow() == row:
            self._update_cover_preview(data)
            self.cover_status.setText(f"Cover loaded (row {row+1})\nDouble-click image to pick")

    def _on_cell_clicked(self, row, col):
        data = self._covers.get(row)
        if data:
            self._update_cover_preview(data)
            self.cover_status.setText(f"Row {row+1} cover\nDouble-click image or button to pick")
        elif row < len(self._results) and self._results[row].get('cover_id'):
            self._update_cover_preview(None); self.cover_status.setText(f"Row {row+1}: cover loading…")
        else:
            self._update_cover_preview(None); self.cover_status.setText("No cover for this result")

    def _pick_cover(self):
        row = self.tbl.currentRow(); data = self._covers.get(row)
        if not data: self.cover_status.setText("Cover not loaded yet — wait a moment"); return
        if self._cover_pick == data:
            # Toggle off — picking the same cover again unpicks it
            self._cover_pick = None
            self.cover_preview.setStyleSheet(
                f"border:2px solid #313244; border-radius:4px; color:{GRAY};")
            self.cover_status.setText("Cover unpicked")
            self._update_picks_label()
            return
        self._cover_pick = data
        self.cover_preview.setStyleSheet("border:2px solid #a6e3a1; border-radius:4px;")
        self.cover_status.setText(f"✔ Cover from row {row+1} will be applied "
                                  "(pick again to unpick)")
        self._update_picks_label()

    def _update_cover_preview(self, data: Optional[bytes]):
        if data:
            px = QPixmap(); px.loadFromData(data)
            if not px.isNull():
                px = px.scaled(163, 208, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self.cover_preview.setPixmap(px); self.cover_preview.setText(''); return
        self.cover_preview.clear(); self.cover_preview.setText("No Cover")

    def _on_cell_double_clicked(self, row, col):
        if col == 0: return           # checkbox column
        logical = col - 1
        item = self.tbl.item(row, col)
        if not item: return
        value = item.text().strip()
        if not value: return
        prev = self._picks.get(logical)
        if prev:
            pi = self.tbl.item(prev[0], logical + 1)
            if pi: pi.setBackground(QColor("#181825"))
        if prev == (row, value): del self._picks[logical]
        else:
            self._picks[logical] = (row, value)
            item.setBackground(QColor(CELL_PICKED))
        self._update_picks_label()

    def _on_use_toggled(self, item):
        """The 'Use' checkbox picks/unpicks every field of its row."""
        if self._filling or item.column() != 0: return
        row = item.row()
        checked = item.checkState() == Qt.CheckState.Checked
        self._filling = True
        # Only one row can be 'used' at a time
        if checked:
            for r in range(self.tbl.rowCount()):
                if r != row:
                    other = self.tbl.item(r, 0)
                    if other and other.checkState() == Qt.CheckState.Checked:
                        other.setCheckState(Qt.CheckState.Unchecked)
        self._filling = False

        # Reset current pick highlights
        for logical, (r, _) in self._picks.items():
            pi = self.tbl.item(r, logical + 1)
            if pi: pi.setBackground(QColor("#181825"))
        self._picks.clear()

        if checked:
            for logical in range(len(COL_KEYS)):
                cell = self.tbl.item(row, logical + 1)
                val = cell.text().strip() if cell else ''
                if val:
                    self._picks[logical] = (row, val)
                    cell.setBackground(QColor(CELL_PICKED))
            data = self._covers.get(row)
            if data:
                self._cover_pick = data
                self._update_cover_preview(data)
                self.cover_preview.setStyleSheet("border:2px solid #a6e3a1; border-radius:4px;")
                self.cover_status.setText(f"✔ Cover from row {row+1} will be applied")
        else:
            self._cover_pick = None
            self.cover_preview.setStyleSheet(f"border:2px solid #313244; border-radius:4px; color:{GRAY};")
        self._update_picks_label()

    def _clear_picks(self):
        for logical, (row, _) in self._picks.items():
            item = self.tbl.item(row, logical + 1)
            if item: item.setBackground(QColor("#181825"))
        self._picks.clear(); self._cover_pick = None
        self._filling = True
        for r in range(self.tbl.rowCount()):
            use = self.tbl.item(r, 0)
            if use: use.setCheckState(Qt.CheckState.Unchecked)
        self._filling = False
        self.cover_preview.setStyleSheet(f"border:2px solid #313244; border-radius:4px; color:{GRAY};")
        self._update_picks_label()

    def _update_picks_label(self):
        parts = []
        for col in range(len(COL_NAMES)):
            if col in self._picks:
                row, val = self._picks[col]
                parts.append(f"{COL_NAMES[col]}: '{val[:28]}' (row {row+1})")
        if self._cover_pick: parts.append("Cover: ✔ picked")
        if parts: self.picks_lbl.setText("Will apply: " + "  |  ".join(parts))
        else: self.picks_lbl.setText("No fields picked yet — double-click cells above")

    def get_selected(self) -> Optional[dict]:
        out = {}
        for col, key in enumerate(COL_KEYS):
            if col in self._picks: out[key] = self._picks[col][1]
        if self._cover_pick: out['cover_art'] = self._cover_pick
        return out if out else None

    def closeEvent(self, event):
        self._stop_cover_thread(); super().closeEvent(event)


# ─── Raw-tag dialog ───────────────────────────────────────────────────────────

class RawTagDialog(QDialog):
    def __init__(self, af: sc.AudioFile, parent=None):
        super().__init__(parent)
        self.af = af; self.setWindowTitle(f"Raw Tags — {af.filename}")
        self.setMinimumSize(560, 460); self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        self.tbl = QTableWidget(0, 2); self.tbl.setHorizontalHeaderLabels(["Tag", "Value"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        lay.addWidget(self.tbl); self._load()
        br = QHBoxLayout()
        purge = QPushButton("Purge Non-Standard Tags"); purge.clicked.connect(self._purge)
        br.addWidget(purge); br.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.accept); br.addWidget(close)
        lay.addLayout(br)

    def _load(self):
        self.tbl.setRowCount(0)
        try: tags = tg.read_all_tags_raw(self.af.path)
        except Exception as e: tags = {"error": str(e)}
        for key, val in sorted(tags.items()):
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            self.tbl.setItem(r, 0, QTableWidgetItem(str(key)))
            self.tbl.setItem(r, 1, QTableWidgetItem(str(val)[:300]))

    def _purge(self):
        ans = QMessageBox.question(self, "Purge Tags",
            "Remove non-standard tags? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            removed = tg.purge_extra_tags(self.af.path)
            QMessageBox.information(self, "Done", f"Removed {len(removed)} tag(s).")
            self._load()


# ─── Problems dialog ──────────────────────────────────────────────────────────

def _suggest_fix(err: str, path: Path) -> str:
    """Human advice for a scan problem, matched on the error text."""
    e = err.lower()
    full = str(path).lower()
    if path.name.startswith('._') or '__macosx' in full:
        return "macOS sidecar junk, not audio — safe to delete"
    if ('atom' in e or 'chapter' in e or 'unpack requires a buffer' in e
            or 'moov' in e):
        return ("Damaged container structure — click Repair (lossless remux, "
                "audio untouched, original goes to the Recycle Bin)")
    if ('not a mp4 file' in e or 'not a valid flac file' in e
            or 'sync to mpeg' in e or "can't sync" in e):
        return ("Extension doesn't match the actual content, or the download "
                "is incomplete — try Repair; if that fails, re-download")
    if 'permission' in e or 'access is denied' in e:
        return "File locked or read-only — close other apps / check permissions"
    if 'no such file' in e:
        return "File vanished since the scan started — rescan"
    return "Try the Repair button (lossless remux); if that fails, re-download"


class ProblemsDialog(QDialog):
    """Lists files whose tags failed to read during scanning."""
    def __init__(self, problems: list, parent=None):
        # problems: [(source_label, Path, error_str), …]
        super().__init__(parent)
        self.setWindowTitle("Scan Problems")
        self.setMinimumSize(1180, 420)
        self._repairs: list = []
        lay = QVBoxLayout(self)

        tip = QLabel("These files could not be read properly — usually corrupt or "
                     "mis-named files.  Double-click a row to open its folder.  "
                     "Repair rebuilds the file container losslessly with ffmpeg "
                     "(audio untouched, original goes to the Recycle Bin).")
        tip.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["Source", "File", "Error", "Suggested Fix", ""])
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setWordWrap(True)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.cellDoubleClicked.connect(self._open_row_folder)
        self._paths: list = []
        for source, path, err in problems:
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            self.tbl.setItem(r, 0, QTableWidgetItem(source))
            fi = QTableWidgetItem(path.name); fi.setToolTip(str(path))
            self.tbl.setItem(r, 1, fi)
            ei = QTableWidgetItem(err); ei.setForeground(QColor(RED))
            ei.setToolTip(err)
            self.tbl.setItem(r, 2, ei)
            fix = _suggest_fix(err, path)
            si = QTableWidgetItem(fix); si.setForeground(QColor(GREEN))
            si.setToolTip(fix)
            self.tbl.setItem(r, 3, si)
            btn = QPushButton("🔧 Repair")
            junk = path.name.startswith('._') or '__macosx' in str(path).lower()
            if junk:
                btn.setEnabled(False)
                btn.setToolTip("Sidecar junk — just delete it")
            else:
                btn.setToolTip("Lossless ffmpeg remux — original goes to the Recycle Bin")
                btn.clicked.connect(lambda checked=False, row=r: self._repair_row(row))
            self.tbl.setCellWidget(r, 4, btn)
            self._paths.append(path)
        self.tbl.resizeRowsToContents()
        lay.addWidget(self.tbl)

        br = QHBoxLayout(); br.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        br.addWidget(close); lay.addLayout(br)

    def _open_row_folder(self, row, col):
        if 0 <= row < len(self._paths):
            try:
                import os; os.startfile(str(self._paths[row].parent))
            except Exception:
                pass

    def _repair_row(self, row):
        if not (0 <= row < len(self._paths)): return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            QMessageBox.warning(self, "ffmpeg Not Found",
                "Repair needs ffmpeg — keep ffmpeg.exe next to the app.")
            return
        w = self.tbl.cellWidget(row, 4)
        if w: w.setEnabled(False)
        fx = self.tbl.item(row, 3)
        if fx:
            fx.setText("Repairing (lossless remux)…")
            fx.setForeground(QColor(YELLOW))
        t = RepairThread(self._paths[row], ffmpeg)
        t.done.connect(self._on_repair_done)
        self._repairs.append(t)
        t.start()

    def _on_repair_done(self, path, ok, msg):
        log_line(f"[repair] {path}: {'OK' if ok else 'FAILED'} — {msg}")
        try:
            row = self._paths.index(path)
        except ValueError:
            return
        fx = self.tbl.item(row, 3)
        if fx:
            fx.setText(("✔ " if ok else "✖ ") + msg)
            fx.setForeground(QColor(GREEN if ok else RED))
            fx.setToolTip(msg)
        err_item = self.tbl.item(row, 2)
        if ok and err_item:
            err_item.setForeground(QColor(GRAY))
        w = self.tbl.cellWidget(row, 4)
        if w: w.setEnabled(not ok)   # leave enabled for a retry on failure


# ─── Duplicate results dialog ─────────────────────────────────────────────────

class DupResultsDialog(QDialog):
    """Duplicate-check results with per-copy delete buttons."""
    def __init__(self, results: list, delete_cb, parent=None):
        # results: [(verdict, book_a, book_b)] — delete_cb(list[Book]) -> bool
        super().__init__(parent)
        self._delete_cb = delete_cb
        self.setWindowTitle("Duplicate Check Results")
        self.setMinimumSize(1180, 480)
        lay = QVBoxLayout(self)

        tip = QLabel("Double-click a Copy A / Copy B path to open it in Explorer.  "
                     "The Delete buttons move that copy's files straight to the "
                     "Recycle Bin — no confirmation popup, restore from the Bin "
                     "if you change your mind.")
        tip.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.tbl = QTableWidget(len(results), 5)
        self.tbl.setHorizontalHeaderLabels(["Verdict", "Book", "Copy A", "Copy B", "Actions"])
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(False)
        for col, wpx in enumerate([220, 230, 280, 280, 175]):
            self.tbl.setColumnWidth(col, wpx)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.cellDoubleClicked.connect(self._open_in_explorer)
        self._row_paths: list = []   # row → (folder_a, folder_b)

        for r, (verdict, a, b) in enumerate(results):
            vi = QTableWidgetItem(verdict)
            vi.setForeground(QColor(RED if 'EXACT' in verdict else YELLOW))
            vi.setToolTip(verdict)
            self.tbl.setItem(r, 0, vi)
            bi = QTableWidgetItem(f"{a.display_name} — {a.author or 'Unknown'}")
            self.tbl.setItem(r, 1, bi)
            folder_a = a.files[0].path.parent
            folder_b = b.files[0].path.parent
            pa = QTableWidgetItem(str(folder_a))
            pa.setToolTip(f"{folder_a}\n(double-click to open in Explorer)")
            self.tbl.setItem(r, 2, pa)
            pb = QTableWidgetItem(str(folder_b))
            pb.setToolTip(f"{folder_b}\n(double-click to open in Explorer)")
            self.tbl.setItem(r, 3, pb)
            self._row_paths.append((folder_a, folder_b))

            w = QWidget(); wl = QHBoxLayout(w)
            wl.setContentsMargins(2, 0, 2, 0); wl.setSpacing(4)
            for label, book in (("Delete A", a), ("Delete B", b)):
                btn = QPushButton(label)
                btn.setStyleSheet(BTN_ACCENT)
                btn.clicked.connect(
                    lambda checked=False, bk=book, row=r: self._delete(bk, row))
                wl.addWidget(btn)
            self.tbl.setCellWidget(r, 4, w)
        lay.addWidget(self.tbl)

        br = QHBoxLayout(); br.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        br.addWidget(close); lay.addLayout(br)

    def _open_in_explorer(self, row, col):
        """Double-click on a Copy A / Copy B path opens that folder."""
        if col not in (2, 3) or not (0 <= row < len(self._row_paths)):
            return
        folder = self._row_paths[row][col - 2]
        try:
            import os; os.startfile(str(folder))
        except Exception:
            pass

    def _delete(self, book, row):
        if not self._delete_cb([book]):
            return
        for c in range(4):
            it = self.tbl.item(row, c)
            if it: it.setForeground(QColor(GRAY))
        vi = self.tbl.item(row, 0)
        if vi: vi.setText(f"♻ recycled '{book.display_name}' copy")
        w = self.tbl.cellWidget(row, 4)
        if w: w.setEnabled(False)


# ─── Import-folder settings dialog ────────────────────────────────────────────

class ImportSettingsDialog(QDialog):
    def __init__(self, enabled: bool, folder: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Folder")
        self.setMinimumWidth(540)
        lay = QVBoxLayout(self)

        tip = QLabel(
            "The Import folder is a separate scan source for new or unsorted books.\n"
            "When enabled, its contents appear in a second tree above the Library tree.\n"
            "Files are NOT moved automatically — use the Move / Organise tab to relocate them\n"
            "into the Library folder."
        )
        tip.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.enable_cb = QCheckBox("Enable Import folder")
        self.enable_cb.setChecked(enabled)
        lay.addWidget(self.enable_cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("Folder:"))
        self.path_edit = QLineEdit(folder)
        self.path_edit.setPlaceholderText("Select a folder to scan for unsorted books…")
        row.addWidget(self.path_edit)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)

        br = QHBoxLayout(); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("OK"); ok.setStyleSheet(BTN_PRIMARY); ok.clicked.connect(self.accept)
        br.addWidget(cancel); br.addWidget(ok)
        lay.addLayout(br)

    def _browse(self):
        start = self.path_edit.text().strip() or str(Path.home())
        f = QFileDialog.getExistingDirectory(self, "Select Import Folder", start)
        if f: self.path_edit.setText(f)

    def get(self) -> tuple:
        return self.enable_cb.isChecked(), self.path_edit.text().strip()


# ─── Add-Tag dialog ───────────────────────────────────────────────────────────

class AddTagDialog(QDialog):
    """Add a new extra tag to the selected file(s)."""

    # Suggested tag names — picking one fills in a per-format default
    SUGGESTIONS = [
        # (display_name, mp3_key,           mp4_key,                            vorbis_key)
        ("ASIN",       "TXXX:ASIN",       "----:com.apple.iTunes:ASIN",       "asin"),
        ("ISBN",       "TXXX:ISBN",       "----:com.apple.iTunes:ISBN",       "isbn"),
        ("Subtitle",   "TIT3",            "----:com.apple.iTunes:SUBTITLE",   "subtitle"),
        ("Composer",   "TCOM",            "\xa9wrt",                          "composer"),
        ("Copyright",  "TCOP",            "cprt",                             "copyright"),
        ("Language",   "TLAN",            "----:com.apple.iTunes:LANGUAGE",   "language"),
        ("Mood",       "TMOO",            "----:com.apple.iTunes:MOOD",       "mood"),
        ("Rating",     "TXXX:RATING",     "----:com.apple.iTunes:RATING",     "rating"),
        ("Website",    "WOAS",            "----:com.apple.iTunes:WEBSITE",    "website"),
    ]

    def __init__(self, file_ext: str = '', parent=None):
        super().__init__(parent)
        self.file_ext = file_ext.lower()
        self.setWindowTitle("Add Extra Tag")
        self.setMinimumWidth(580)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        tip = QLabel(
            "Add a new tag to all selected file(s).  Pick a common tag from the "
            "dropdown (auto-filled for your file format) or type a custom key.\n\n"
            "Format examples:\n"
            "• MP3 (ID3):     TXXX:MYTAG     or     TIT3\n"
            "• MP4 (M4B):    ----:com.apple.iTunes:MYTAG     or     ©nam\n"
            "• FLAC / OGG:  mytag  (lowercase)")
        tip.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # Quick-pick row
        qp = QHBoxLayout()
        qp.addWidget(QLabel("Quick pick:"))
        self.suggest_cb = QComboBox()
        self.suggest_cb.addItem("— custom —")
        for name, *_ in self.SUGGESTIONS:
            self.suggest_cb.addItem(name)
        self.suggest_cb.currentIndexChanged.connect(self._apply_suggestion)
        qp.addWidget(self.suggest_cb); qp.addStretch()
        lay.addLayout(qp)

        # Tag row
        row1 = QHBoxLayout()
        l1 = QLabel("Tag:"); l1.setMinimumWidth(60); row1.addWidget(l1)
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("e.g. TXXX:ASIN  •  ----:com.apple.iTunes:ASIN  •  asin")
        row1.addWidget(self.tag_edit)
        lay.addLayout(row1)

        # Value row
        row2 = QHBoxLayout()
        l2 = QLabel("Value:"); l2.setMinimumWidth(60); row2.addWidget(l2)
        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("Value to store")
        row2.addWidget(self.value_edit)
        lay.addLayout(row2)

        br = QHBoxLayout(); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Add Tag"); ok.setStyleSheet(BTN_PRIMARY)
        ok.clicked.connect(self.accept)
        br.addWidget(cancel); br.addWidget(ok)
        lay.addLayout(br)

    def _apply_suggestion(self, idx):
        if idx <= 0: return
        _, mp3_k, mp4_k, vorb_k = self.SUGGESTIONS[idx - 1]
        if self.file_ext == '.mp3':
            self.tag_edit.setText(mp3_k)
        elif self.file_ext in ('.m4b', '.m4a', '.aac', '.mp4', '.alac'):
            self.tag_edit.setText(mp4_k)
        elif self.file_ext in ('.flac', '.ogg', '.opus'):
            self.tag_edit.setText(vorb_k)
        else:
            self.tag_edit.setText(vorb_k)

    def get(self) -> tuple:
        return self.tag_edit.text().strip(), self.value_edit.text().strip()


# ─── Edit Metadata tab ────────────────────────────────────────────────────────

class EditMetadataTab(QWidget):
    """
    Per-field editing with ▾ tag picker and per-field Apply button.
    Works on Book objects; marks them modified and optionally saves to disk.
    """
    save_books_requested   = pyqtSignal(list)   # list[sc.Book]
    status_message         = pyqtSignal(str)
    tree_refresh_requested = pyqtSignal()
    fields_applied         = pyqtSignal(list)   # books whose folder may be stale

    FIELDS = [
        ("Title",           "title"),
        ("Author",          "author"),
        ("Narrator",        "narrator"),
        ("Series",          "series"),
        ("Series #",        "series_num"),
        ("Year",            "year"),
        ("Publisher",       "publisher"),
        ("Genre",           "genre"),
    ]

    FIELD_TOOLTIPS = {
        'title':      "Book title\n• MP3:     TIT2  (+ TALB album)\n• MP4/M4B: ©nam  (+ ©alb)\n• FLAC/OGG: title (+ album)",
        'author':     "Book author\n• MP3:     TPE1\n• MP4/M4B: ©ART\n• FLAC/OGG: artist",
        'narrator':   "Narrator / reader\n• MP3:     TPE2\n• MP4/M4B: aART\n• FLAC/OGG: performer",
        'series':     "Series name\n• MP3:     TIT1 (grouping)\n• MP4/M4B: ----:com.apple.iTunes:SERIES\n• FLAC/OGG: series",
        'series_num': "Position in series — e.g. 1, 2, 3.5\n• MP3:     TXXX:SERIES_INDEX (+ TXXX:series_num)\n• MP4/M4B: ----:com.apple.iTunes:SERIES-PART\n• FLAC/OGG: series-part",
        'year':       "Publication / release year\n• MP3:     TDRC\n• MP4/M4B: ©day\n• FLAC/OGG: date",
        'publisher':  "Publisher\n• MP3:     TPUB\n• MP4/M4B: ----:com.apple.iTunes:PUBLISHER\n• FLAC/OGG: organization",
        'genre':      "Genre\n• MP3:     TCON\n• MP4/M4B: ©gen\n• FLAC/OGG: genre",
    }
    DESC_TOOLTIP = ("Long-form description / comment\n"
                    "• MP3:     COMM\n"
                    "• MP4/M4B: desc / ©cmt\n"
                    "• FLAC/OGG: description / comment")

    _CSS_COVER_NORMAL  = f"border:1px solid #313244; border-radius:4px; color:{GRAY};"
    _CSS_COVER_PENDING = f"border:2px solid {YELLOW}; border-radius:4px;"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._books: List[sc.Book] = []
        self._busy = False
        self._field_edits: dict = {}   # key → QLineEdit
        self._extra_edits: list = []   # [(key, original_value, QLineEdit)]
        self._pending_cover: Optional[bytes] = None
        self.all_books_provider = None   # callable → all books (library + import)
        self._build_ui()

    # ── public ────────────────────────────────────────────────────

    def set_books(self, books: List[sc.Book]):
        self._books = books
        if books:
            self._load_from_book(books[0])
        else:
            self._clear_fields()
        self._rebuild_extra_tags()
        n = len(books)
        if n == 0:
            self._note_lbl.setText("No books selected.")
        elif n == 1:
            self._note_lbl.setText(f"1 book selected — {books[0].display_name}")
        else:
            self._note_lbl.setText(
                f"{n} books selected. ⚠ The big Save writes ALL fields (Title too!) — "
                "use a field's Apply button to change just that field.")
        self._save_btn.setText(f"Save to {n} Book(s)" if n else "Save to Selected Books")
        self._update_cover_info()

    def _update_cover_info(self):
        covers = {c for b in self._books for af in b.files
                  if (c := af.tags.get('cover_art'))}
        if len(covers) > 1:
            self._cover_info_lbl.setText(
                f"🖼 {len(covers)} different file covers — kept on save. "
                "'Apply Cover' replaces them all.")
        else:
            self._cover_info_lbl.setText("")

    # ── build ─────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Top area: cover (left) + fields (right) ───────────────
        top = QHBoxLayout()

        # Left column: note label, cover, action buttons
        left = QVBoxLayout()
        left.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._note_lbl = QLabel("No books selected.")
        self._note_lbl.setStyleSheet(f"color:{GRAY}; font-size:11px;")
        self._note_lbl.setWordWrap(True)
        self._note_lbl.setMaximumWidth(170)
        left.addWidget(self._note_lbl)

        self._cover_lbl = _ClickableLabel("No Cover")
        self._cover_lbl.setFixedSize(160, 160)
        self._cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_lbl.setStyleSheet(self._CSS_COVER_NORMAL)
        self._cover_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cover_lbl.setToolTip("Click to view the cover full-size")
        self._cover_lbl.clicked.connect(self._view_cover_fullsize)
        left.addWidget(self._cover_lbl)

        set_btn = QPushButton("Set Cover…")
        set_btn.setToolTip("Pick an image — it shows as pending (yellow border) until you Apply")
        set_btn.clicked.connect(self._set_cover)
        left.addWidget(set_btn)

        self._apply_cover_btn = QPushButton("Apply Cover")
        self._apply_cover_btn.setEnabled(False)
        self._apply_cover_btn.setToolTip("Write the pending cover to all selected books and save to disk")
        self._apply_cover_btn.clicked.connect(self._apply_cover)
        left.addWidget(self._apply_cover_btn)

        clear_btn = QPushButton("Clear Cover")
        clear_btn.setToolTip("Remove the cover from the selected books (unsaved until Ctrl+S)")
        clear_btn.clicked.connect(self._clear_cover)
        left.addWidget(clear_btn)

        self._cover_info_lbl = QLabel("")
        self._cover_info_lbl.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        self._cover_info_lbl.setWordWrap(True)
        self._cover_info_lbl.setMaximumWidth(170)
        left.addWidget(self._cover_info_lbl)

        self._search_btn = QPushButton("Search Metadata…")
        self._search_btn.setEnabled(False)
        self._search_btn.clicked.connect(self._search_internet)
        left.addWidget(self._search_btn)

        left.addStretch()
        top.addLayout(left)

        # Right column: form fields
        right = QVBoxLayout()

        grid = QGridLayout()
        grid.setSpacing(4)

        for row, (label, key) in enumerate(self.FIELDS):
            lbl = QLabel(label + ":"); lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setMinimumWidth(80)
            tip = self.FIELD_TOOLTIPS.get(key, "")
            if tip: lbl.setToolTip(tip)
            grid.addWidget(lbl, row, 0)

            edit = QLineEdit(); self._field_edits[key] = edit
            if tip: edit.setToolTip(tip)
            grid.addWidget(edit, row, 1)

            picker_btn = QPushButton("▾")
            picker_btn.setMaximumWidth(28)
            picker_btn.setToolTip("Pick a value from the file's existing tags")
            picker_btn.clicked.connect(lambda checked, k=key: self._show_tag_picker(k))
            grid.addWidget(picker_btn, row, 2)

            apply_btn = QPushButton("Apply")
            apply_btn.setMaximumWidth(60)
            apply_btn.setToolTip(
                f"Write ONLY {label} to every selected book and save to disk.\n"
                "Safe with multiple books — no other field is touched.")
            apply_btn.clicked.connect(lambda checked, k=key: self._apply_field(k))
            grid.addWidget(apply_btn, row, 3)

            if key == 'title':
                parse_btn = QPushButton("Parse")
                parse_btn.setMaximumWidth(60)
                parse_btn.setToolTip(
                    "Split series info out of the title.\n"
                    'Understands:  "The Inquisition (Summoner, #2)"\n'
                    '"Title (Series, Book 2)"  •  "Series #2 - Title"')
                parse_btn.clicked.connect(self._parse_title)
                grid.addWidget(parse_btn, row, 4)

        # Description row (spans full width, no per-field apply)
        desc_lbl = QLabel("Description:"); desc_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        desc_lbl.setMinimumWidth(80)
        desc_lbl.setToolTip(self.DESC_TOOLTIP)
        grid.addWidget(desc_lbl, len(self.FIELDS), 0)
        self._desc_edit = QTextEdit(); self._desc_edit.setMaximumHeight(70)
        self._desc_edit.setToolTip(self.DESC_TOOLTIP)
        grid.addWidget(self._desc_edit, len(self.FIELDS), 1, 1, 3)

        grid.setColumnStretch(1, 1)
        right.addLayout(grid)

        opt_row = QHBoxLayout()
        self._keep_existing = QCheckBox("Keep existing value when field is blank")
        self._keep_existing.setChecked(True)
        opt_row.addWidget(self._keep_existing)
        opt_row.addStretch()
        self._save_btn = QPushButton("Save to Selected Books")
        self._save_btn.setStyleSheet(BTN_PRIMARY)
        self._save_btn.setEnabled(False)
        self._save_btn.setToolTip("Write every field above to all selected books and save to disk")
        self._save_btn.clicked.connect(self._save_all_fields)
        opt_row.addWidget(self._save_btn)
        right.addLayout(opt_row)
        right.addStretch()

        top.addLayout(right)
        root.addLayout(top)

        # ── Extra tags section ────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#313244;"); root.addWidget(sep)

        xhdr = QHBoxLayout()
        xl = QLabel("Extra tags (not covered above):")
        xl.setStyleSheet(f"color:{BLUE}; font-weight:bold;")
        xhdr.addWidget(xl)
        add_tag_btn = QPushButton("➕ Add Tag")
        add_tag_btn.setToolTip("Add a brand-new tag (custom key + value) to the selected files")
        add_tag_btn.clicked.connect(self._add_extra_tag)
        save_extra_btn = QPushButton("Save Edits")
        save_extra_btn.setStyleSheet(BTN_PRIMARY)
        save_extra_btn.setToolTip("Write edited extra-tag values back to the selected files")
        save_extra_btn.clicked.connect(self._save_extra_edits)
        purge_btn = QPushButton("Purge All Extra Tags")
        purge_btn.setStyleSheet(BTN_ACCENT)
        purge_btn.setToolTip("Removes non-standard tags from selected files (cover art kept)")
        purge_btn.clicked.connect(self._purge_extra_tags)
        xhdr.addStretch(); xhdr.addWidget(add_tag_btn); xhdr.addWidget(save_extra_btn); xhdr.addWidget(purge_btn)
        root.addLayout(xhdr)

        self._extra_scroll = QScrollArea()
        self._extra_scroll.setWidgetResizable(True)
        self._extra_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._extra_scroll.setMaximumHeight(140)
        self._extra_inner = QWidget()
        self._extra_layout = QGridLayout(self._extra_inner)
        self._extra_layout.setSpacing(3)
        self._extra_scroll.setWidget(self._extra_inner)
        root.addWidget(self._extra_scroll)

    # ── field helpers ─────────────────────────────────────────────

    def _load_from_book(self, book: sc.Book):
        self._busy = True
        self._field_edits['title'].setText(book.title)
        self._field_edits['author'].setText(book.author)
        self._field_edits['narrator'].setText(book.narrator)
        self._field_edits['series'].setText(book.series)
        self._field_edits['series_num'].setText(book.series_num)
        self._field_edits['year'].setText(book.year)
        self._field_edits['publisher'].setText(book.publisher)
        self._field_edits['genre'].setText(book.genre)
        self._desc_edit.setPlainText(book.description)
        self._reset_pending_cover()
        self._show_cover(book.cover_art)
        self._save_btn.setEnabled(True)
        self._search_btn.setEnabled(True)
        self._busy = False

    def _clear_fields(self):
        self._busy = True
        for edit in self._field_edits.values(): edit.clear()
        self._reset_pending_cover()
        self._desc_edit.clear(); self._show_cover(None)
        self._save_btn.setEnabled(False); self._search_btn.setEnabled(False)
        self._busy = False

    def _current_values(self) -> dict:
        vals = {k: e.text().strip() for k, e in self._field_edits.items()}
        vals['description'] = self._desc_edit.toPlainText().strip()
        return vals

    # ── cover ─────────────────────────────────────────────────────

    def _show_cover(self, data: Optional[bytes]):
        self._shown_cover_bytes = data
        if data:
            px = QPixmap(); px.loadFromData(data)
            if not px.isNull():
                px = px.scaled(158, 158, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self._cover_lbl.setPixmap(px); self._cover_lbl.setText(''); return
        self._cover_lbl.clear(); self._cover_lbl.setText("No Cover")

    def _view_cover_fullsize(self):
        data = getattr(self, '_shown_cover_bytes', None)
        if data:
            CoverViewDialog(data, self).exec()

    def _set_cover(self):
        if not self._books: return
        first_file = next((af for b in self._books for af in b.files), None)
        start_dir = str(first_file.path.parent) if first_file else ""
        path, _ = QFileDialog.getOpenFileName(self, "Select Cover Image", start_dir,
            "Images (*.jpg *.jpeg *.png *.webp *.bmp)")
        if not path: return
        self._pending_cover = Path(path).read_bytes()
        self._show_cover(self._pending_cover)
        self._cover_lbl.setStyleSheet(self._CSS_COVER_PENDING)
        self._apply_cover_btn.setEnabled(True)
        self.status_message.emit(
            "Cover is PENDING (yellow border) — click 'Apply Cover' to save it "
            f"to {len(self._books)} book(s).")

    def _apply_cover(self):
        if not self._books or self._pending_cover is None: return
        for book in self._books:
            book.cover_art = self._pending_cover
            book.cover_explicit = True   # overwrite per-file covers on save
            book.modified = True
        n = len(self._books)
        self._reset_pending_cover()
        self.save_books_requested.emit(list(self._books))
        self.status_message.emit(f"Cover applied and saved to {n} book(s).")

    def _clear_cover(self):
        self._reset_pending_cover()
        for book in self._books:
            book.cover_art = None; book.modified = True
        self._show_cover(None)
        self.status_message.emit(
            f"Cover cleared on {len(self._books)} book(s) — unsaved (Ctrl+S to write).")

    def _reset_pending_cover(self):
        self._pending_cover = None
        self._apply_cover_btn.setEnabled(False)
        self._cover_lbl.setStyleSheet(self._CSS_COVER_NORMAL)

    # ── tag picker ────────────────────────────────────────────────

    def _show_tag_picker(self, field_key: str):
        if not self._books: return
        first_file = next((af for b in self._books for af in b.files), None)
        if not first_file: return

        raw = tg.read_all_tags_raw(first_file.path)
        menu = QMenu(self)

        fname_action = menu.addAction(f"Filename:  {first_file.filename}")
        fname_action.setData(first_file.path.stem)

        if raw:
            menu.addSeparator()
            seen = set()
            for k, v in sorted(raw.items()):
                val = str(v).strip()
                if not val or val in seen: continue
                seen.add(val)
                display = val if len(val) <= 60 else val[:57] + "…"
                act = menu.addAction(f"{k}:  {display}")
                act.setData(val)
        else:
            menu.addSeparator()
            menu.addAction("(no tags found)").setEnabled(False)

        # Values from the rest of the collection: for the Author field offer
        # every known author; otherwise offer this author's values from their
        # other books (e.g. pick an existing Series for an untagged book).
        all_books = self.all_books_provider() if self.all_books_provider else []
        book = self._books[0]
        suggestions = []
        if field_key == 'author':
            header = "— all authors in collection —"
            seen_vals = set()
            for b in all_books:
                val = (b.author or '').strip()
                if val and val.lower() not in seen_vals:
                    seen_vals.add(val.lower())
                    suggestions.append(val)
        else:
            author = (book.author or '').strip().lower()
            header = f"— from {book.author}'s other books —" if author else ""
            if author:
                seen_vals = set()
                for b in all_books:
                    if b in self._books: continue
                    if (b.author or '').strip().lower() != author: continue
                    val = str(getattr(b, field_key, '') or '').strip()
                    if val and val.lower() not in seen_vals:
                        seen_vals.add(val.lower())
                        suggestions.append(val)
        if suggestions:
            menu.addSeparator()
            menu.addAction(header).setEnabled(False)
            for val in sorted(suggestions, key=str.casefold)[:25]:
                display = val if len(val) <= 60 else val[:57] + "…"
                act = menu.addAction(f"📚  {display}")
                act.setData(val)

        edit_widget = self._field_edits.get(field_key)
        pos = edit_widget.mapToGlobal(QPoint(0, edit_widget.height())) if edit_widget else QCursor.pos()
        chosen = menu.exec(pos)
        if chosen and chosen.data() is not None:
            self._field_edits[field_key].setText(chosen.data())

    # ── apply / save ──────────────────────────────────────────────

    def _apply_field(self, field_key: str):
        """Write ONE field to every selected book — nothing else is written,
        not even covers."""
        if not self._books:
            QMessageBox.information(self, "No Selection", "Select at least one book first.")
            return
        value = self._field_edits[field_key].text().strip()
        label = field_key.replace('_', ' ').title()
        if not value:
            ans = QMessageBox.question(self, "Clear Field",
                f"The {label} field is empty — REMOVE the {label} tag from "
                f"every file of {len(self._books)} selected book(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes: return
        for book in self._books:
            setattr(book, field_key, value)
        done_msg = (f"{label} cleared from" if not value
                    else f"{label} = '{value[:40]}' written to")
        path_relevant  = field_key in ('title', 'author', 'series', 'series_num')
        books_snapshot = list(self._books)

        def _done(n, m=done_msg):
            self.status_message.emit(f"{m} {n} file(s).")
            self.tree_refresh_requested.emit()
            if path_relevant:
                self.fields_applied.emit(books_snapshot)

        self._field_thread = FieldSaveThread(books_snapshot, field_key, value)
        self._field_thread.finished.connect(_done)
        self._field_thread.error.connect(
            lambda e: self.status_message.emit(f"Error writing {label}: {e}"))
        self._field_thread.start()
        self.status_message.emit(f"{'Clearing' if not value else 'Writing'} {label} "
                                 f"on {len(self._books)} book(s)…")

    def _save_all_fields(self):
        if not self._books:
            QMessageBox.information(self, "No Selection", "Select at least one book first.")
            return
        vals = self._current_values()
        keep = self._keep_existing.isChecked()
        for book in self._books:
            for key, value in vals.items():
                if value or not keep:
                    setattr(book, key, value)
            book.modified = True
        self.save_books_requested.emit(list(self._books))
        self.status_message.emit(f"All fields saved to {len(self._books)} book(s).")

    # ── parse title ───────────────────────────────────────────────

    def _parse_title(self):
        if not self._books: return
        raw = self._field_edits['title'].text().strip()
        if not raw: return
        parsed = parse_audiobook_title(raw)
        if len(parsed) <= 1:
            QMessageBox.information(self, "Parse Title",
                'No series pattern detected.\n\nExpects formats like:\n'
                '"The Inquisition (Summoner, #2)"\n"Series #N - Title"')
            return
        if 'title' in parsed:    self._field_edits['title'].setText(parsed['title'])
        if 'series' in parsed and not self._field_edits['series'].text():
            self._field_edits['series'].setText(parsed['series'])
        if 'series_num' in parsed and not self._field_edits['series_num'].text():
            self._field_edits['series_num'].setText(parsed['series_num'])

    # ── open library ──────────────────────────────────────────────

    def _search_internet(self):
        if not self._books: return
        dlg = OpenLibraryDialog(self._books[0], self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        r = dlg.get_selected()
        if not r: return
        self._busy = True
        for key in COL_KEYS:
            if r.get(key):
                self._field_edits[key].setText(str(r[key]))
        self._busy = False

        # "Apply Picked Fields" commits everything in one go — fields AND
        # cover are written to the selected books, no second Apply needed.
        picked = {k: str(r[k]) for k in COL_KEYS if r.get(k)}
        cover  = r.get('cover_art')
        if not picked and not cover:
            return
        for book in self._books:
            for k, v in picked.items():
                setattr(book, k, v)
            if cover:
                book.cover_art = cover
                book.cover_explicit = True
            book.modified = True
        if cover:
            self._reset_pending_cover()
            self._show_cover(cover)
        self.save_books_requested.emit(list(self._books))
        parts = []
        if picked: parts.append(f"{len(picked)} field(s)")
        if cover:  parts.append("cover")
        self.status_message.emit(
            f"Applied {' + '.join(parts)} to {len(self._books)} book(s) — saving…")

    # ── extra tags ────────────────────────────────────────────────

    def _rebuild_extra_tags(self):
        # Clear existing widgets
        while self._extra_layout.count():
            item = self._extra_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._extra_edits: list = []   # [(key, original_value, QLineEdit)]

        if not self._books: return
        first_file = next((af for b in self._books for af in b.files), None)
        if not first_file: return

        # The Extras panel and Purge share tagger.is_standard_tag — what you
        # see here is exactly what Purge removes.
        raw = tg.read_all_tags_raw(first_file.path)
        extras = [(k, v) for k, v in raw.items()
                  if not tg.is_standard_tag(k) and 'covr' not in k.lower()]

        if not extras:
            lbl = QLabel("No extra tags found.")
            lbl.setStyleSheet(f"color:{GRAY}; font-size:11px;")
            self._extra_layout.addWidget(lbl, 0, 0)
            return

        for row, (k, v) in enumerate(extras):
            klbl = QLabel(k + ":"); klbl.setStyleSheet(f"color:{GRAY}; font-family:Consolas;")
            klbl.setMinimumWidth(200); klbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._extra_layout.addWidget(klbl, row, 0)
            original = str(v)[:200]
            vedit = QLineEdit(original)
            self._extra_layout.addWidget(vedit, row, 1)
            self._extra_edits.append((k, original, vedit))

        self._extra_layout.setColumnStretch(1, 1)

    def _add_extra_tag(self):
        if not self._books:
            QMessageBox.information(self, "No Selection", "Select at least one book first.")
            return
        first_file = next((af for b in self._books for af in b.files), None)
        ext = first_file.ext if first_file else ''
        dlg = AddTagDialog(file_ext=ext, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        key, value = dlg.get()
        if not key:
            QMessageBox.warning(self, "Missing Tag", "Tag key is required.")
            return
        paths = [af.path for b in self._books for af in b.files]
        ok = 0; fail = 0
        for path in paths:
            if tg.add_raw_tag(path, key, value): ok += 1
            else: fail += 1
        self._rebuild_extra_tags()
        msg = f"Added '{key}' = '{value[:30]}{'…' if len(value) > 30 else ''}' to {ok} file(s)."
        if fail: msg += f"  ({fail} failed)"
        self.status_message.emit(msg)

    def _save_extra_edits(self):
        if not self._books:
            self.status_message.emit("No books selected.")
            return
        changed = [(k, e.text()) for (k, orig, e) in self._extra_edits
                   if e.text() != orig]
        if not changed:
            self.status_message.emit("No extra-tag changes to save.")
            return
        paths = [af.path for b in self._books for af in b.files]
        ok = 0; fail = 0
        for path in paths:
            for key, new_val in changed:
                if tg.update_raw_tag(path, key, new_val): ok += 1
                else: fail += 1
        self._rebuild_extra_tags()
        msg = f"Saved {ok} extra-tag write(s) across {len(paths)} file(s)."
        if fail: msg += f"  ({fail} skipped — tag not present in those files)"
        self.status_message.emit(msg)

    def _purge_extra_tags(self):
        if not self._books: return
        paths = [af.path for b in self._books for af in b.files]
        ans = QMessageBox.question(self, "Purge Extra Tags",
            f"Remove non-standard tags from {len(paths)} file(s)?\n"
            "Cover art will be kept. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes: return
        removed = 0
        for path in paths:
            try: removed += len(tg.purge_extra_tags(path))
            except Exception: pass
        self._rebuild_extra_tags()
        self.status_message.emit(f"Purged {removed} extra tag(s) from {len(paths)} file(s).")


# ─── Files tab ────────────────────────────────────────────────────────────────

class _FileTable(QTableWidget):
    """File table that drags rows out (to the tree) and accepts drops on
    itself for reordering, with a live insertion-line indicator."""
    def __init__(self, owner: 'FilesTab'):
        super().__init__(0, 4)
        self._owner = owner
        self._drop_line = -1   # gap index the line is drawn at; -1 = hidden

    def _mime_ok(self, mime) -> bool:
        return mime.hasFormat(MIME_FILES) or mime.hasFormat(MIME_FILEBLOCK)

    def _gap_for_pos(self, pos) -> int:
        """Insertion gap for a viewport position: 0..rowCount().
        Above a row's midpoint → before it; below → after it."""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return self.rowCount()
        row = idx.row()
        if pos.y() > self.visualRect(idx).center().y():
            row += 1
        return row

    def _set_drop_line(self, gap: int):
        if gap != self._drop_line:
            self._drop_line = gap
            self.viewport().update()

    def startDrag(self, supported_actions):
        self._owner._start_drag(supported_actions)

    def dragEnterEvent(self, event):
        if self._mime_ok(event.mimeData()): event.acceptProposedAction()
        else: super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._mime_ok(event.mimeData()):
            self._set_drop_line(self._gap_for_pos(event.position().toPoint()))
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._set_drop_line(-1)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        gap = self._gap_for_pos(event.position().toPoint())
        self._set_drop_line(-1)
        if event.mimeData().hasFormat(MIME_FILEBLOCK):
            self._owner._drop_block(event, gap)
        elif event.mimeData().hasFormat(MIME_FILES):
            self._owner._drop_reorder(event, gap)
        else:
            super().dropEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_line < 0: return
        if self._drop_line >= self.rowCount():
            if self.rowCount() == 0:
                y = 1
            else:
                y = self.visualRect(self.model().index(self.rowCount() - 1, 0)).bottom() + 1
        else:
            y = self.visualRect(self.model().index(self._drop_line, 0)).top()
        painter = QPainter(self.viewport())
        pen = QPen(QColor(BLUE)); pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(0, y, self.viewport().width(), y)


class FilesTab(QWidget):
    """File list + reorder + rename + merge + split, consolidated in one tab."""
    status_message      = pyqtSignal(str)
    merge_requested     = pyqtSignal(list)             # list[sc.Book] to merge
    ops_performed       = pyqtSignal(str, list, bool)  # desc, pairs, is_copy
    build_m4b_requested = pyqtSignal(object)           # sc.Book
    split_requested     = pyqtSignal(list)             # [(book, file_idx), …]
    autosplit_requested = pyqtSignal(object)           # sc.Book → split by album tag

    def __init__(self, parent=None):
        super().__init__(parent)
        self.book: Optional[sc.Book] = None
        self._selected_books: List[sc.Book] = []
        self._row_map: list = []      # row → (book, file_idx) or ('sep', book)
        self._collapsed: set = set()  # book ids whose file rows are hidden
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4)
        hdr = QHBoxLayout()
        self._files_lbl = QLabel("Files in this book")
        self._files_lbl.setStyleSheet(f"color:{GRAY}; font-weight:bold;")
        hdr.addWidget(self._files_lbl)
        hint = QLabel("  Drag rows to reorder  •  drag a ── block ── row to move the whole book  •  drag onto a tree book to move files there")
        hint.setStyleSheet(f"color:{GRAY}; font-size:10px; font-style:italic;")
        hdr.addWidget(hint); hdr.addStretch()
        self._merge_btn = QPushButton("🔗 Merge Selected Books")
        self._merge_btn.setToolTip(
            "Combine all books selected in the tree into the first one.\n"
            "Reorder the files below afterwards, then Save All Tags.")
        self._merge_btn.setEnabled(False)
        self._merge_btn.clicked.connect(
            lambda: self.merge_requested.emit(list(self._selected_books)))
        hdr.addWidget(self._merge_btn)
        self._split_btn = QPushButton("✂ Split")
        self._split_btn.setToolTip(
            "Split a book that was scanned as one but is really several:\n"
            "• Select file rows, then split them into a new book\n"
            "• Or auto-split by each file's album tag")
        split_menu = QMenu(self._split_btn)
        split_menu.addAction("Split selected files into a new book").triggered.connect(
            self._request_split_selected)
        split_menu.addAction("Auto-split this book by album tag").triggered.connect(
            lambda: self.autosplit_requested.emit(self.book))
        self._split_btn.setMenu(split_menu)
        hdr.addWidget(self._split_btn)
        self._m4b_btn = QPushButton("Build M4B…")
        self._m4b_btn.setToolTip(
            "Combine this book's files into a single .m4b with chapters.\n"
            "Each file becomes one chapter. Requires ffmpeg on PATH.\n"
            "Originals are untouched.")
        self._m4b_btn.clicked.connect(
            lambda: self.build_m4b_requested.emit(self.book))
        hdr.addWidget(self._m4b_btn)
        for lbl2, tip, slot in [
                ("↑ Up",   "", self._move_up),
                ("↓ Down", "", self._move_down),
                ("− All",  "Collapse all book blocks to one line", self._collapse_all),
                ("+ All",  "Expand all book blocks", self._expand_all),
                ("Sort by Filename", "", self._sort_by_filename),
                ("Inspect Tags", "", self._inspect)]:
            b = QPushButton(lbl2); b.clicked.connect(slot)
            if tip: b.setToolTip(tip)
            hdr.addWidget(b)
        lay.addLayout(hdr)

        self.tbl = _FileTable(self)
        self.tbl.setHorizontalHeaderLabels(["#", "Current Filename", "New Name Preview", "Duration"])
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setDragEnabled(True)
        self.tbl.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tbl.setDropIndicatorShown(True)
        self.tbl.setAutoScroll(True)
        self.tbl.setAutoScrollMargin(50)
        lay.addWidget(self.tbl)

        # ── Rename section ────────────────────────────────────────
        ren_grp = QGroupBox("Rename Files")
        rl = QVBoxLayout(ren_grp)
        pat_row = QHBoxLayout()
        pat_row.addWidget(QLabel("Pattern:"))
        self._pattern_edit = QLineEdit("{title}")
        pat_row.addWidget(self._pattern_edit)
        self._use_counter = QCheckBox("Number files")
        self._use_counter.setChecked(True)
        self._use_counter.setToolTip("Append {n} when the pattern doesn't include it")
        pat_row.addWidget(self._use_counter)
        pat_row.addWidget(QLabel("Start:"))
        self._counter_start = QSpinBox(); self._counter_start.setRange(0, 9999)
        self._counter_start.setValue(1); self._counter_start.setMaximumWidth(70)
        pat_row.addWidget(self._counter_start)
        self._rename_btn = QPushButton("Apply Rename")
        self._rename_btn.setStyleSheet(BTN_PRIMARY)
        self._rename_btn.clicked.connect(self._apply_rename)
        pat_row.addWidget(self._rename_btn)
        rl.addLayout(pat_row)
        hint2 = QLabel("Placeholders: {title}  {author}  {series}  {series_num}  {year}  {narrator}  {n}"
                       "   •   preview in the 'New Name' column above"
                       "   •   single-file books get no number")
        hint2.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        hint2.setWordWrap(True)
        rl.addWidget(hint2)
        lay.addWidget(ren_grp)

        self._pattern_edit.textChanged.connect(self.refresh)
        self._use_counter.stateChanged.connect(self.refresh)
        self._counter_start.valueChanged.connect(self.refresh)

    def set_book(self, book):
        self.book = book
        if len(self._selected_books) < 2:
            self.refresh()

    def set_books(self, books: List[sc.Book]):
        self._selected_books = books or []
        n = len(self._selected_books)
        self._merge_btn.setEnabled(n >= 2)
        self._merge_btn.setText(f"🔗 Merge {n} Selected Books" if n >= 2
                                else "🔗 Merge Selected Books")
        self._rename_btn.setText(f"Apply Rename to {n} Books" if n > 1 else "Apply Rename")
        self.refresh()

    def _displayed_books(self) -> List[sc.Book]:
        if len(self._selected_books) > 1:
            return self._selected_books
        return [self.book] if self.book else []

    # ── rename helpers ────────────────────────────────────────────

    def _build_new_name(self, pattern: str, book: sc.Book, filename: str,
                        counter: int, pad: int = 1) -> str:
        stem = pattern
        replacements = {
            '{title}':      book.title,
            '{author}':     book.author,
            '{series}':     book.series,
            '{series_num}': book.series_num,
            '{year}':       book.year,
            '{narrator}':   book.narrator,
        }
        for placeholder, value in replacements.items():
            stem = stem.replace(placeholder, value or "")
        if '{n}' in stem:
            stem = stem.replace('{n}', str(counter).zfill(pad))
        return _sanitize(stem) or Path(filename).stem

    def _new_names_for(self, book: sc.Book) -> list:
        """New filename stems for every file of *book*, per the pattern controls."""
        pattern     = self._pattern_edit.text() or "{title}"
        use_counter = self._use_counter.isChecked()
        start       = self._counter_start.value()
        n_files     = len(book.files)
        single      = n_files <= 1
        pad         = len(str(start + n_files - 1)) if n_files else 1
        stems = []
        counter = start
        for af in book.files:
            pat = pattern
            if use_counter and not single and '{n}' not in pat:
                pat += " {n}"
            stems.append(self._build_new_name(pat, book, af.filename, counter, pad))
            counter += 1
        return stems

    def refresh(self):
        self.tbl.clearSpans()
        self.tbl.setRowCount(0)
        self._row_map = []
        books = self._displayed_books()
        multi = len(books) > 1
        if multi:
            self._files_lbl.setText(f"Files in {len(books)} selected books")
        else:
            self._files_lbl.setText("Files in this book")
        for book in books:
            collapsed = book.id in self._collapsed
            if multi:
                r = self.tbl.rowCount(); self.tbl.insertRow(r)
                btn = QPushButton("+" if collapsed else "−")
                btn.setFixedSize(24, 20)
                btn.setStyleSheet("padding:0; font-weight:bold;")
                btn.setToolTip("Collapse / expand this book's files")
                btn.clicked.connect(lambda _, bid=book.id: self._toggle_block(bid))
                self.tbl.setCellWidget(r, 0, btn)
                dur = f"  [{book.duration_str()}]" if book.duration_str() else ""
                sep = QTableWidgetItem(f"{book.display_name}  ({book.file_count} files){dur}")
                sep.setForeground(QColor(PEACH))
                sep.setToolTip("Drag this row to move the whole block — merges follow this order")
                sep.setFlags(Qt.ItemFlag.ItemIsEnabled
                             | Qt.ItemFlag.ItemIsSelectable
                             | Qt.ItemFlag.ItemIsDragEnabled)
                self.tbl.setItem(r, 1, sep)
                self.tbl.setSpan(r, 1, 1, 3)
                self._row_map.append(('sep', book))
                if collapsed:
                    continue
            stems = self._new_names_for(book)
            for i, (af, stem) in enumerate(zip(book.files, stems), 1):
                r = self.tbl.rowCount(); self.tbl.insertRow(r)
                self.tbl.setItem(r, 0, QTableWidgetItem(str(i)))
                self.tbl.setItem(r, 1, QTableWidgetItem(af.filename))
                pi = QTableWidgetItem(stem + af.ext); pi.setForeground(QColor(GREEN))
                self.tbl.setItem(r, 2, pi)
                self.tbl.setItem(r, 3, QTableWidgetItem(af.duration_str()))
                self._row_map.append((book, i - 1))

    def _row_target(self, row: int):
        """Return (book, file_idx) for a table row, or None for separators/invalid."""
        if 0 <= row < len(self._row_map):
            entry = self._row_map[row]
            if entry and entry[0] != 'sep':
                return entry
        return None

    def _row_book(self, row: int):
        """Return the book a row belongs to — works for separator rows too."""
        if 0 <= row < len(self._row_map):
            entry = self._row_map[row]
            if entry:
                return entry[1] if entry[0] == 'sep' else entry[0]
        return None

    def _toggle_block(self, bid: str):
        if bid in self._collapsed: self._collapsed.discard(bid)
        else: self._collapsed.add(bid)
        self.refresh()

    def _collapse_all(self):
        self._collapsed = {b.id for b in self._displayed_books()}
        self.refresh()

    def _expand_all(self):
        self._collapsed.clear()
        self.refresh()

    def _apply_rename(self):
        books = [b for b in self._displayed_books() if b.files]
        if not books:
            QMessageBox.information(self, "Nothing to rename", "Select at least one book first.")
            return

        # Only rename the highlighted file rows when there's a selection;
        # numbering still follows each file's position in the whole book.
        sel = set()
        for idx in self.tbl.selectedIndexes():
            t = self._row_target(idx.row())
            if t: sel.add((id(t[0]), t[1]))

        total_files = len(sel) if sel else sum(b.file_count for b in books)
        if not total_files: return
        scope = (f"{total_files} selected file(s)" if sel else
                 f"all {total_files} file(s) across {len(books)} book(s)")
        ans = QMessageBox.question(self, "Confirm Rename",
            f"Rename {scope}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes: return

        renamed = 0; errors = []; performed = []
        for book in books:
            for i, (af, stem) in enumerate(zip(book.files, self._new_names_for(book))):
                if sel and (id(book), i) not in sel: continue
                new_path = af.path.parent / (stem + af.ext)
                if new_path == af.path: continue
                try:
                    old = af.path
                    af.path.rename(new_path)
                    af.path = new_path
                    renamed += 1
                    performed.append((old, new_path))
                except Exception as e:
                    errors.append(f"{af.filename}: {e}")

        self.ops_performed.emit(f"Renamed {renamed} file(s)", performed, False)
        if errors:
            QMessageBox.warning(self, "Some renames failed", "\n".join(errors[:10]))
        self.status_message.emit(f"Renamed {renamed} file(s).")
        self.refresh()

    def _request_split_selected(self):
        rows = sorted({i.row() for i in self.tbl.selectedIndexes()})
        pairs = [t for r in rows if (t := self._row_target(r))]
        if not pairs:
            QMessageBox.information(self, "Split",
                "Select one or more file rows first, then split them off.")
            return
        self.split_requested.emit(pairs)

    def _current_af(self):
        t = self._row_target(self.tbl.currentRow())
        return t[0].files[t[1]] if t else None

    def _move_up(self):
        row = self.tbl.currentRow()
        t = self._row_target(row)
        if not t: return
        book, idx = t
        if idx <= 0: return   # already first in its book
        f = book.files; f[idx-1], f[idx] = f[idx], f[idx-1]
        book.modified = True
        self.refresh(); self.tbl.selectRow(row - 1)

    def _move_down(self):
        row = self.tbl.currentRow()
        t = self._row_target(row)
        if not t: return
        book, idx = t
        if idx >= book.file_count - 1: return   # already last in its book
        f = book.files; f[idx], f[idx+1] = f[idx+1], f[idx]
        book.modified = True
        self.refresh(); self.tbl.selectRow(row + 1)

    def _sort_by_filename(self):
        """Natural-sort files by name in every displayed book — handy after merges."""
        sorted_files = 0
        for book in self._displayed_books():
            if book.file_count >= 2:
                book.files.sort(key=lambda af: sc._natural_key(af.path))
                book.modified = True
                sorted_files += book.file_count
        if sorted_files:
            self.refresh()
            self.status_message.emit(f"Sorted {sorted_files} file(s) by filename.")
        else:
            self.status_message.emit("Nothing to sort.")

    def _inspect(self):
        af = self._current_af()
        if af: RawTagDialog(af, self).exec()

    def _start_drag(self, supported_actions):
        rows = sorted({idx.row() for idx in self.tbl.selectedIndexes()})

        # Any separator row in the selection → drag whole block(s)
        block_ids = []
        for r in rows:
            if 0 <= r < len(self._row_map):
                entry = self._row_map[r]
                if entry and entry[0] == 'sep' and entry[1].id not in block_ids:
                    block_ids.append(entry[1].id)
        if block_ids:
            mime = QMimeData()
            mime.setData(MIME_FILEBLOCK, QByteArray(json.dumps(block_ids).encode()))
            drag = QDrag(self.tbl); drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)
            return

        payload = []
        for r in rows:
            t = self._row_target(r)
            if t: payload.append({"src_book_id": t[0].id, "file_index": t[1]})
        if not payload: return
        mime = QMimeData()
        mime.setData(MIME_FILES, QByteArray(json.dumps(payload).encode()))
        drag = QDrag(self.tbl); drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def _drop_block(self, event, gap: int):
        """Reorder whole book blocks in the multi-book view. Files stay in
        their books; the merge button follows this displayed order."""
        try:
            block_ids = json.loads(bytes(event.mimeData().data(MIME_FILEBLOCK)).decode())
        except Exception:
            event.ignore(); return
        books = self._selected_books
        moving = [b for b in books if b.id in block_ids]
        if not moving or len(books) < 2:
            event.ignore(); return

        # Which book does the gap anchor to — and before or after it?
        if gap >= len(self._row_map):
            anchor, after = None, True
        else:
            entry = self._row_map[gap]
            if entry[0] == 'sep':               # gap at a block header → before that block
                anchor, after = entry[1], False
            else:
                book, idx = entry               # inside a block: start → before, else after
                anchor, after = book, idx > 0

        remaining = [b for b in books if b.id not in block_ids]
        if anchor is None or anchor.id in block_ids:
            pos = len(remaining)
        else:
            pos = remaining.index(anchor) + (1 if after else 0)
        self._selected_books = remaining[:pos] + moving + remaining[pos:]

        event.acceptProposedAction()
        self.refresh()
        self.status_message.emit(
            "Blocks reordered — merging will combine them in this order.")

    def _drop_reorder(self, event, gap: int):
        """Drop dragged rows back onto the table → reorder (also across books)."""
        try:
            payload = json.loads(bytes(event.mimeData().data(MIME_FILES)).decode())
        except Exception:
            event.ignore(); return
        books = self._displayed_books()
        if not books: event.ignore(); return
        by_id = {b.id: b for b in books}

        # Map the insertion gap to (target book, insertion index within its files)
        if gap >= len(self._row_map):
            tgt_book, insert_idx = books[-1], books[-1].file_count
        else:
            entry = self._row_map[gap]
            if entry[0] == 'sep':
                # Gap right before a block header → end of whatever block sits
                # above it (works for collapsed blocks too); at the very top →
                # start of that header's own block
                prev_book = self._row_book(gap - 1) if gap > 0 else None
                if prev_book is not None:
                    tgt_book, insert_idx = prev_book, prev_book.file_count
                else:
                    tgt_book, insert_idx = entry[1], 0
            else:
                tgt_book, insert_idx = entry

        # Collect dragged (book, index) refs, restricted to displayed books
        per_src: dict = {}
        for entry in payload:
            per_src.setdefault(entry["src_book_id"], []).append(entry["file_index"])
        moving: list = []
        for src_id, indices in per_src.items():
            src = by_id.get(src_id)
            if src is None: continue
            for idx in sorted(set(indices)):
                if 0 <= idx < len(src.files):
                    moving.append((src, idx))
        if not moving: event.ignore(); return

        # Files removed above the insertion point shift it up
        removed_before = sum(1 for src, idx in moving
                             if src is tgt_book and idx < insert_idx)

        # Keep the visual order of the dragged block, then extract (high→low per book)
        display_order = {id(b): i for i, b in enumerate(books)}
        moving.sort(key=lambda t: (display_order.get(id(t[0]), 0), t[1]))
        files_moved = [src.files[idx] for src, idx in moving]
        for src, idx in sorted(moving, key=lambda t: t[1], reverse=True):
            src.files.pop(idx)
            src.modified = True
        tgt_book.modified = True
        insert_at = insert_idx - removed_before
        for offset, af in enumerate(files_moved):
            tgt_book.files.insert(insert_at + offset, af)

        event.acceptProposedAction()
        self.refresh()
        self.status_message.emit(f"Reordered {len(files_moved)} file(s).")


# ─── Move / Organise tab ──────────────────────────────────────────────────────

class MoveOrganiseTab(QWidget):
    status_message   = pyqtSignal(str)
    rescan_requested = pyqtSignal()
    ops_performed    = pyqtSignal(str, list, bool)   # desc, pairs, is_copy

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_books: List[sc.Book] = []
        self._all_books:      List[sc.Book] = []
        self._group_rows: list = []   # [{author_cb, series_cb, book_edit, num_edit, preview_lbl, book}]
        self._org_thread: Optional[OrganizeThread] = None
        self._build_ui()

    def set_books(self, selected: List[sc.Book], all_books: List[sc.Book]):
        self._selected_books = selected
        self._all_books = all_books
        self._refresh_groups()

    def set_default_destination(self, folder: str):
        """Pre-fill the destination field with the library folder."""
        if folder:
            self._dest_edit.setText(folder)
            self._update_all_previews()

    def _build_ui(self):
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8)

        # Destination row
        dest_grp = QGroupBox("File Destination")
        dest_row = QHBoxLayout(dest_grp)
        self._dest_edit = QLineEdit(); self._dest_edit.setPlaceholderText("Select destination folder…")
        dest_row.addWidget(self._dest_edit)
        browse_btn = QPushButton("Browse…"); browse_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(browse_btn)
        hint = QLabel("Dest / Author / Series / Book")
        hint.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        dest_row.addWidget(hint)
        lay.addWidget(dest_grp)

        # Options row
        opts = QHBoxLayout()
        self._copy_cb = QCheckBox("Copy files (keep originals)")
        self._skip_cb = QCheckBox("Skip if destination already exists")
        self._skip_cb.setToolTip(
            "Ticked: colliding files stay at the source.\n"
            "Unticked: colliding files are auto-renamed like 'Name (2).mp3'.\n"
            "Existing files are NEVER overwritten either way.")
        self._skip_cb.setChecked(True)
        opts.addWidget(self._copy_cb); opts.addWidget(self._skip_cb); opts.addStretch()
        lay.addLayout(opts)

        # Group list header
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Groups  (one row per selected book — updates automatically):"))
        hdr.addStretch()
        move_btn = QPushButton("Move / Copy All Groups")
        move_btn.setStyleSheet(BTN_PRIMARY)
        move_btn.clicked.connect(self._move_files)
        hdr.addWidget(move_btn)
        lay.addLayout(hdr)

        # Column headers
        col_hdr = QWidget()
        col_hdr.setStyleSheet(f"background:#313244;")
        ch = QHBoxLayout(col_hdr); ch.setContentsMargins(4, 2, 4, 2)
        for text, stretch in [("Book", 3), ("Author", 2), ("Series", 2), ("Folder Name", 2), ("#", 1)]:
            l = QLabel(text); l.setStyleSheet(f"color:{BLUE}; font-weight:bold; font-size:11px;")
            ch.addWidget(l, stretch)
        lay.addWidget(col_hdr)

        # Scrollable group rows
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._rows_widget)
        lay.addWidget(self._scroll)

        self._placeholder = QLabel("Select books in a tree — a row appears here for each selected book.")
        self._placeholder.setStyleSheet(f"color:{GRAY}; font-style:italic; padding:12px;")
        self._rows_layout.insertWidget(0, self._placeholder)

    def _browse_dest(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if folder:
            self._dest_edit.setText(folder)
            self._update_all_previews()

    def _known_authors(self):
        return sorted({(b.author or "").strip() for b in self._all_books if b.author})

    def _known_series(self):
        return sorted({(b.series or "").strip() for b in self._all_books if b.series})

    def _refresh_groups(self):
        # Remove old rows (keep stretch at end)
        while self._rows_layout.count() > 0:
            item = self._rows_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._group_rows = []

        if not self._selected_books:
            self._placeholder = QLabel("Select books in a tree — a row appears here for each selected book.")
            self._placeholder.setStyleSheet(f"color:{GRAY}; font-style:italic; padding:12px;")
            self._rows_layout.addWidget(self._placeholder)
            self._rows_layout.addStretch()
            return

        authors = self._known_authors()
        series_list = self._known_series()

        for book in self._selected_books:
            row_w = QWidget()
            row_w.setStyleSheet("background:#181825; border-radius:4px;")
            rl = QVBoxLayout(row_w); rl.setContentsMargins(6, 4, 6, 4); rl.setSpacing(2)

            fields_row = QHBoxLayout()

            book_lbl = QLabel(f"🎧 {book.display_name}  ({book.file_count} file(s))")
            book_lbl.setStyleSheet(f"color:{YELLOW};"); book_lbl.setMinimumWidth(160)
            fields_row.addWidget(book_lbl, 3)

            author_cb = QComboBox(); author_cb.setEditable(True)
            author_cb.addItems(authors)
            author_cb.setCurrentText(book.author or "")
            fields_row.addWidget(author_cb, 2)

            series_cb = QComboBox(); series_cb.setEditable(True)
            series_cb.addItems([""] + series_list)
            series_cb.setCurrentText(book.series or "")
            fields_row.addWidget(series_cb, 2)

            folder_edit = QLineEdit(book.title or "")
            fields_row.addWidget(folder_edit, 2)

            num_edit = QLineEdit(book.series_num or "")
            num_edit.setMaximumWidth(50)
            fields_row.addWidget(num_edit, 1)

            rl.addLayout(fields_row)

            preview_lbl = QLabel("")
            preview_lbl.setStyleSheet(f"color:{BLUE}; font-family:Consolas; font-size:10px;")
            preview_lbl.setWordWrap(True)
            rl.addWidget(preview_lbl)

            row_dict = {
                'author': author_cb, 'series': series_cb,
                'folder': folder_edit, 'num': num_edit,
                'preview': preview_lbl, 'book': book,
            }
            self._group_rows.append(row_dict)

            def make_updater(rd):
                def _update(*_):
                    dest = self._dest_edit.text().strip()
                    author = _sanitize(rd['author'].currentText().strip())
                    series = _sanitize(rd['series'].currentText().strip())
                    num    = rd['num'].text().strip()
                    folder = _sanitize(rd['folder'].text().strip())
                    if num and folder:
                        book_folder = f"{num}-{folder}"
                    elif num:
                        book_folder = num
                    else:
                        book_folder = folder
                    if dest and author:
                        parts = [dest, author]
                        if series: parts.append(series)
                        if book_folder: parts.append(book_folder)
                        path_str = str(Path(*parts))
                        rd['preview'].setText("→  " + path_str)
                    else:
                        rd['preview'].setText("")
                return _update

            updater = make_updater(row_dict)
            author_cb.currentTextChanged.connect(updater)
            series_cb.currentTextChanged.connect(updater)
            folder_edit.textChanged.connect(updater)
            num_edit.textChanged.connect(updater)
            self._dest_edit.textChanged.connect(updater)
            updater()

            self._rows_layout.addWidget(row_w)

        self._rows_layout.addStretch()

    def _update_all_previews(self):
        for rd in self._group_rows:
            rd['author'].currentTextChanged.emit(rd['author'].currentText())

    def _move_files(self):
        if not self._group_rows:
            QMessageBox.information(self, "No Groups", "Select one or more books in a tree first.")
            return
        dest = self._dest_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "No Destination", "Set a destination folder first.")
            return

        # Build move list using organizer
        moves = []
        for rd in self._group_rows:
            book   = rd['book']
            author = _sanitize(rd['author'].currentText().strip()) or 'Unknown Author'
            series = _sanitize(rd['series'].currentText().strip())
            num    = rd['num'].text().strip()
            folder = _sanitize(rd['folder'].text().strip())

            target_dir = org.build_folder_path(dest, author, series, num, folder or book.title)
            total = book.file_count
            for i, af in enumerate(book.files, 1):
                stem = org.build_file_name(folder or book.title, str(i), str(total))
                dst  = target_dir / (stem + af.ext)
                moves.append((af.path, dst))

        copy_mode = self._copy_cb.isChecked()
        skip      = self._skip_cb.isChecked()
        self._org_thread = OrganizeThread(moves, copy_mode, skip)
        self._org_thread.finished.connect(self._on_move_done)
        self._org_thread.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self._org_thread.start()
        self.status_message.emit(f"{'Copying' if copy_mode else 'Moving'} {len(moves)} files…")

    def _on_move_done(self, done, errors, performed, skipped, renamed):
        copy_mode = self._copy_cb.isChecked()
        if performed:
            self.ops_performed.emit(
                f"{'Copied' if copy_mode else 'Moved'} {len(performed)} file(s)",
                performed, copy_mode)
        for src, dst in renamed:
            log_line(f"RENAMED TO AVOID OVERWRITE: {src} → {dst}")
        for src, dst in skipped:
            log_line(f"SKIPPED (destination exists): {src} → {dst}")
        notes = []
        if skipped:
            notes.append(f"{len(skipped)} skipped — destination already exists:")
            notes += [f"  {d.name}" for _, d in skipped[:5]]
        if renamed:
            notes.append(f"{len(renamed)} auto-renamed to avoid overwriting:")
            notes += [f"  {d.name}" for _, d in renamed[:5]]
        if errors:
            QMessageBox.warning(self, "Completed with Errors",
                f"{done} file(s) processed, {len(errors)} error(s):\n"
                + "\n".join(errors[:8])
                + ("\n\n" + "\n".join(notes) if notes else ""))
        else:
            QMessageBox.information(self, "Done",
                f"{'Copied' if copy_mode else 'Moved'} {len(performed)} file(s)."
                + ("\n\n" + "\n".join(notes) if notes else ""))
        self.status_message.emit(f"Move/copy complete — {len(performed)} file(s).")
        self.rescan_requested.emit()


# ─── Main window ──────────────────────────────────────────────────────────────

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
        self.setWindowTitle("AudioBook Manager v2")
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
        for verdict, a, b in results:
            log_line(f"[dupcheck] {verdict} | '{a.display_name}' | "
                     f"{a.files[0].path.parent} | {b.files[0].path.parent}")
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


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    _rotate_log()
    log_line("=== AudioBook Manager v2 started ===")
    app = QApplication(sys.argv)
    app.setApplicationName("AudioBook Manager v2")
    app.setOrganizationName("ABMv2")
    app.setStyleSheet(STYLE)
    win = MainWindow(); win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
