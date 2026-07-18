"""
scanner.py – Scans folders and groups audio files into Book objects.

Grouping priority:
  1. Files in their own subfolder → one book per subfolder
  2. Files in root folder        → grouped by 'album' tag, then by filename prefix
"""

import re
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable

AUDIO_EXTENSIONS = {
    '.mp3', '.m4b', '.m4a', '.flac', '.ogg', '.opus',
    '.aac', '.wma', '.wav', '.aiff', '.ape', '.mp4', '.alac',
}

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
_COVER_NAMES = ('cover', 'folder', 'front', 'albumart')

ProgressCallback = Callable[[int, int, str], None]


# ──────────────────────────────────────────────────────────────────
#  Data model
# ──────────────────────────────────────────────────────────────────

@dataclass
class AudioFile:
    path: Path
    tags: dict = field(default_factory=dict)
    duration: float = 0.0
    hydrated: bool = False   # True once tags/duration were read from disk

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def ext(self) -> str:
        return self.path.suffix.lower()

    def duration_str(self) -> str:
        if self.duration <= 0:
            return '--:--'
        h = int(self.duration // 3600)
        m = int((self.duration % 3600) // 60)
        s = int(self.duration % 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Book:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    files: list = field(default_factory=list)   # list[AudioFile]

    # Editable metadata
    title:       str = ''
    author:      str = ''
    narrator:    str = ''
    series:      str = ''
    series_num:  str = ''
    year:        str = ''
    publisher:   str = ''
    description: str = ''
    genre:       str = ''
    cover_art: Optional[bytes] = None
    # True when the user explicitly applied a cover — save then overwrites
    # every file's art. False → each file keeps its own embedded cover.
    cover_explicit: bool = False
    modified:  bool = False

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def display_name(self) -> str:
        if self.title:
            return self.title
        if self.files:
            return self.files[0].path.stem
        return 'Unknown Book'

    @property
    def total_duration(self) -> float:
        return sum(f.duration for f in self.files)

    def duration_str(self) -> str:
        t = self.total_duration
        if t <= 0:
            return ''
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        return f"{h}h {m:02d}m" if h else f"{m}m"


# ──────────────────────────────────────────────────────────────────
#  Scanning
# ──────────────────────────────────────────────────────────────────

def scan_folder(folder_path: str,
                progress_cb: Optional[ProgressCallback] = None,
                book_cb: Optional[callable] = None,
                problems: Optional[list] = None,
                fast: bool = False) -> list:
    """Scan *folder_path* and return a sorted list of Book objects.

    *book_cb*, if provided, is called with each Book as soon as it is
    created — before the final sort — so callers can populate a UI
    progressively while scanning continues in the background.

    *problems*, if provided, is filled with (Path, error_str) tuples for
    files whose tags could not be read.
    """
    import tagger as tg

    root = Path(folder_path)

    # Collect all audio files
    all_audio: list[Path] = sorted(
        p for p in root.rglob('*')
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )

    if not all_audio:
        return []

    # Group paths by their immediate parent folder
    folder_map: dict[Path, list[Path]] = {}
    for p in all_audio:
        folder_map.setdefault(p.parent, []).append(p)

    books: list[Book] = []
    folders = list(folder_map.items())
    total = len(folders)

    for idx, (folder, paths) in enumerate(folders):
        if progress_cb:
            progress_cb(idx, total, f"Scanning {folder.name}…")

        if folder == root:
            # Sub-group root-level files
            for group_paths in _group_root_files(paths, tg).values():
                book = _make_book(sorted(group_paths, key=_natural_key), tg, root, problems, fast)
                books.append(book)
                if book_cb: book_cb(book)
        else:
            book = _make_book(sorted(paths, key=_natural_key), tg, root, problems, fast)
            books.append(book)
            if book_cb: book_cb(book)

    books.sort(key=lambda b: (b.author.lower(), b.title.lower()))

    if progress_cb:
        progress_cb(total, total, 'Done')

    return books


# ──────────────────────────────────────────────────────────────────
#  Internal helpers
# ──────────────────────────────────────────────────────────────────

def _group_root_files(paths: list, tg) -> dict:
    """Group root-level files by album tag; fall back to filename prefix."""
    groups: dict[str, list] = {}
    for path in paths:
        tags = tg.read_tags(path)
        album = tags.get('album', '').strip()
        key = album if album else _strip_track_num(path.stem) or path.stem
        groups.setdefault(key, []).append(path)
    return groups


def _make_book(paths: list, tg, root: Optional[Path] = None,
               problems: Optional[list] = None, fast: bool = False) -> Book:
    """Create a Book from a sorted list of audio paths.

    With *fast* set, only the first file's tags are read here — the rest
    stay unhydrated (AudioFile.hydrated == False) for a background pass.
    """
    book = Book()
    for n, path in enumerate(paths):
        if fast and n > 0:
            book.files.append(AudioFile(path=path))
            continue
        tags = tg.read_tags(path)
        err = tags.pop('_error', None)
        if err is not None and problems is not None:
            problems.append((path, err))
        af = AudioFile(path=path, tags=tags,
                       duration=float(tags.get('duration', 0)),
                       hydrated=True)
        book.files.append(af)

    if book.files:
        t = book.files[0].tags
        book.title       = t.get('album', '') or _strip_track_num(book.files[0].path.stem)
        book.author      = t.get('author', '') or t.get('artist', '')
        # tagger's 'narrator' already applies the right field priority —
        # don't fall back to composer here (it may just be the author again)
        book.narrator    = t.get('narrator', '')
        book.series      = t.get('series', '') or t.get('grouping', '')
        book.series_num  = t.get('series_num', '') or t.get('series-part', '')
        book.year        = t.get('year', '') or t.get('date', '')
        book.publisher   = t.get('publisher', '')
        book.description = t.get('description', '') or t.get('comment', '')
        book.genre       = t.get('genre', '')
        book.cover_art   = t.get('cover_art')

    # Fall back to the first folder under the scan root when tags are empty.
    # e.g. <root>/Matt Dinniman/DCC3/book.m4b  →  author = "Matt Dinniman"
    if not book.author and root and book.files:
        book.author = _infer_author_from_path(book.files[0].path, root)

    # No embedded cover → look for an image file next to the audio
    if not book.cover_art and book.files:
        book.cover_art = _find_folder_cover(book.files[0].path.parent)

    return book


def _find_folder_cover(folder: Path) -> Optional[bytes]:
    """Return image bytes from the folder — prefers cover/folder/front names,
    then falls back to the largest image file."""
    try:
        imgs = [p for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    except OSError:
        return None
    if not imgs:
        return None

    def rank(p: Path):
        named = any(p.stem.lower().startswith(n) for n in _COVER_NAMES)
        try:    size = p.stat().st_size
        except OSError: size = 0
        return (0 if named else 1, -size)

    imgs.sort(key=rank)
    try:
        data = imgs[0].read_bytes()
        return data if len(data) > 300 else None
    except OSError:
        return None


def _infer_author_from_path(file_path: Path, root: Path) -> str:
    try:
        rel = file_path.relative_to(root)
    except Exception:
        return ''
    parts = rel.parts[:-1]   # exclude the filename
    return parts[0] if parts else ''


def _strip_track_num(stem: str) -> str:
    """Remove leading/trailing track numbers from a filename stem."""
    stem = re.sub(r'^\d+[\s.\-_]+', '', stem)
    stem = re.sub(r'[\s.\-_]+\d+$', '', stem)
    return stem.strip()

def _natural_key(path: Path):
    """Sort key that orders '2' before '10' (natural / human sort)."""
    parts = re.split(r'(\d+)', path.name.lower())
    return [int(p) if p.isdigit() else p for p in parts]
