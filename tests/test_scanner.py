"""Scanner logic: natural sort, name stripping, author inference,
folder-cover detection."""
from pathlib import Path

import scanner as sc


def test_natural_key_orders_numbers_humanly():
    names = ['Part 10.mp3', 'Part 2.mp3', 'Part 1.mp3']
    ordered = sorted((Path(n) for n in names), key=sc._natural_key)
    assert [p.name for p in ordered] == ['Part 1.mp3', 'Part 2.mp3', 'Part 10.mp3']

def test_natural_key_mixed_names_dont_crash():
    names = ['intro.mp3', '01 chapter.mp3', 'E06P1.mp3', 'S06P2.mp3']
    assert len(sorted((Path(n) for n in names), key=sc._natural_key)) == 4


def test_strip_track_num_leading():
    assert sc._strip_track_num('01 - The Title') == 'The Title'

def test_strip_track_num_trailing():
    assert sc._strip_track_num('The Title 03') == 'The Title'


def test_infer_author_from_path():
    root = Path('C:/lib')
    f = root / 'Matt Dinniman' / 'DCC3' / 'file.mp3'
    assert sc._infer_author_from_path(f, root) == 'Matt Dinniman'

def test_infer_author_outside_root_is_empty():
    assert sc._infer_author_from_path(Path('D:/other/file.mp3'), Path('C:/lib')) == ''


def test_folder_cover_prefers_cover_named_file(tmp_path):
    big = tmp_path / 'random.jpg'
    big.write_bytes(b'B' * 5000)
    named = tmp_path / 'cover.jpg'
    named.write_bytes(b'C' * 400)
    assert sc._find_folder_cover(tmp_path) == b'C' * 400

def test_folder_cover_falls_back_to_largest(tmp_path):
    (tmp_path / 'small.jpg').write_bytes(b'a' * 400)
    (tmp_path / 'large.png').write_bytes(b'b' * 9000)
    assert sc._find_folder_cover(tmp_path) == b'b' * 9000

def test_folder_cover_ignores_tiny_images(tmp_path):
    (tmp_path / 'stub.jpg').write_bytes(b'x' * 10)   # under the 300-byte floor
    assert sc._find_folder_cover(tmp_path) is None

def test_folder_cover_none_when_no_images(tmp_path):
    (tmp_path / 'notes.txt').write_text('hi')
    assert sc._find_folder_cover(tmp_path) is None


def test_book_display_name_falls_back_to_filename():
    b = sc.Book()
    b.files.append(sc.AudioFile(path=Path('X/Some Book 01.mp3')))
    assert b.display_name == 'Some Book 01'

def test_book_duration_formatting():
    b = sc.Book()
    b.files.append(sc.AudioFile(path=Path('a.mp3'), duration=3660))
    assert b.duration_str() == '1h 01m'
