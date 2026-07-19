"""Background QThread workers: scanning, hydration, saving, moving, hashing, repair, M4B building."""

from pathlib import Path
from typing import Optional, List
import json
import re
import shutil
import sys

from PyQt6.QtCore import QThread, pyqtSignal

import scanner as sc
import tagger as tg
import openlibrary as ol
import audible as au
import organizer as org

from util import send_to_recycle_bin, _ffesc

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
    finished = pyqtSignal(list)   # [(verdict, book_a, book_b, stats_a, stats_b)]

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
                    verdict = self._compare(a, c)
                    report.append((verdict, a, c,
                                   self._book_stats(a), self._book_stats(c)))
        self.finished.emit(report)

    def _book_stats(self, book) -> dict:
        """Total bytes + runtime for a book. Files skipped by a fast scan are
        hydrated here so the duration is real rather than zero."""
        total_bytes = 0
        for af in book.files:
            try:
                total_bytes += af.path.stat().st_size
            except OSError:
                pass
            if not af.hydrated:
                t = tg.read_tags(af.path)
                t.pop('_error', None)
                af.tags = t
                af.duration = float(t.get('duration', 0) or 0)
                af.hydrated = True
        return {'bytes':   total_bytes,
                'seconds': sum(af.duration for af in book.files),
                'files':   book.file_count}

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
