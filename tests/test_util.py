"""Pure helpers: title parsing, filename sanitizing, ffmetadata escaping,
human-readable size/duration formatting."""
from util import (parse_audiobook_title, _sanitize, _ffesc,
                  fmt_size, fmt_duration)


def test_parse_paren_series_hash():
    out = parse_audiobook_title('The Inquisition (Summoner, #2)')
    assert out == {'title': 'The Inquisition', 'series': 'Summoner',
                   'series_num': '2'}

def test_parse_paren_series_book_word():
    out = parse_audiobook_title('The Novice (Summoner, Book 1)')
    assert out['series'] == 'Summoner' and out['series_num'] == '1'

def test_parse_prefix_series_dash_title():
    out = parse_audiobook_title('Summoner #2 - The Inquisition')
    assert out == {'series': 'Summoner', 'series_num': '2',
                   'title': 'The Inquisition'}

def test_parse_fractional_number():
    out = parse_audiobook_title('Interlude (Dungeon Crawler Carl, #18.5)')
    assert out['series_num'] == '18.5'

def test_parse_plain_title_untouched():
    assert parse_audiobook_title('Just A Title') == {'title': 'Just A Title'}


def test_sanitize_replaces_forbidden_with_underscore():
    assert _sanitize('a:b*c') == 'a_b_c'

def test_sanitize_empty_is_unknown():
    assert _sanitize('   ') == 'Unknown'


def test_ffesc_escapes_metadata_specials():
    assert _ffesc('a=b;c#d\\e') == 'a\\=b\\;c\\#d\\\\e'

def test_ffesc_none_is_empty():
    assert _ffesc(None) == ''


def test_fmt_size_scales_units():
    assert fmt_size(512) == '512 B'
    assert fmt_size(2048) == '2 KB'
    assert fmt_size(5 * 1024 ** 2) == '5 MB'
    assert fmt_size(int(1.5 * 1024 ** 3)) == '1.50 GB'

def test_fmt_size_handles_zero_and_none():
    assert fmt_size(0) == '0 B'
    assert fmt_size(None) == '0 B'


def test_fmt_duration_hours_and_minutes():
    assert fmt_duration(3600) == '1h 00m'
    assert fmt_duration(3600 * 24 + 600) == '24h 10m'

def test_fmt_duration_minutes_only():
    assert fmt_duration(47 * 60) == '47m'

def test_fmt_duration_unknown():
    assert fmt_duration(0) == '--'
    assert fmt_duration(None) == '--'
