"""Small shared helpers: settings, session log, Recycle Bin, ffmpeg lookup, title parsing."""

from pathlib import Path
from typing import Optional, List
import json
import re
import shutil
import sys

def fmt_size(num_bytes) -> str:
    """Human-readable byte count: 734 MB, 1.24 GB …"""
    n = float(num_bytes or 0)
    for unit in ('B', 'KB', 'MB'):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    if n < 1024:
        return f"{n:.2f} GB"
    return f"{n / 1024:.2f} TB"


def fmt_duration(seconds) -> str:
    """Human-readable runtime: 12h 04m, 47m, or -- when unknown."""
    s = int(seconds or 0)
    if s <= 0:
        return '--'
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


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

def _ffesc(val: str) -> str:
    """Escape a value for an ffmetadata file."""
    out = str(val or '')
    for ch in ('\\', '=', ';', '#'):
        out = out.replace(ch, '\\' + ch)
    return out.replace('\n', '\\\n')

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
