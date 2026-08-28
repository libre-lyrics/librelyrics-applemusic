"""Tests for Apple Music AMP catalog/lyrics helpers."""
from __future__ import annotations

from applemusic.module import (
    AMP_API_URL,
    catalog_song_url,
    extract_developer_token,
    find_index_js_paths,
    parse_apple_music_url,
    parse_timestamp,
    parse_ttml,
    song_include_params,
)


def test_catalog_song_url_uses_amp_api_not_public_musickit():
    url = catalog_song_url("in", "1193701400")
    assert url.startswith(AMP_API_URL)
    assert "api.music.apple.com" not in url.replace("amp-api.music.apple.com", "")
    assert url == (
        "https://amp-api.music.apple.com/v1/catalog/in/songs/1193701400"
    )


def test_song_include_params_request_lyrics_on_amp():
    params = song_include_params()
    assert params["include"] == "lyrics,albums"


def test_parse_song_url():
    data = parse_apple_music_url(
        "https://music.apple.com/in/song/perfect/1193701400"
    )
    assert data["region"] == "in"
    assert data["kind"] == "song"
    assert data["track_id"] == "1193701400"


def test_parse_album_track_url():
    data = parse_apple_music_url(
        "https://music.apple.com/us/album/divide/1193701079?i=1193701400"
    )
    assert data["region"] == "us"
    assert data["kind"] == "album"
    assert data["album_id"] == "1193701079"
    assert data["track_id"] == "1193701400"


def test_parse_timestamp_bare_seconds_used_in_apple_ttml():
    assert parse_timestamp("2.629") == 2629
    assert parse_timestamp("2.629s") == 2629
    assert parse_timestamp("1:23.456") == 83456


def test_parse_line_ttml():
    ttml = """
    <tt itunes:timing="Line">
      <body>
        <p begin="2.629" end="8.267">I found a love for me</p>
        <p begin="10.264">Darling, just dive right in</p>
      </body>
    </tt>
    """
    lines, synced, rich = parse_ttml(ttml)
    assert synced is True
    assert rich is False
    assert lines[0].text == "I found a love for me"
    assert lines[0].start_ms == 2629
    assert lines[1].start_ms == 10264


def test_parse_word_ttml():
    ttml = """
    <tt itunes:timing="Word">
      <body>
        <p begin="2.629" end="8.267">
          <span begin="2.629" end="3.117">I</span>
          <span begin="3.117" end="3.483">found</span>
        </p>
      </body>
    </tt>
    """
    lines, synced, rich = parse_ttml(ttml)
    assert synced is True
    assert rich is True
    assert lines[0].text == "I found"
    assert lines[0].words is not None
    assert lines[0].words[0].word == "I"
    assert lines[0].words[0].start_ms == 2629
    assert lines[0].words[0].end_ms == 3117


def test_parse_word_ttml_keeps_syllable_fragments_without_extra_spaces():
    ttml = """
    <tt itunes:timing="Word">
      <body>
        <p begin="21.668" end="24.097">
          <span begin="21.668" end="22.764">Beauti</span><span begin="22.764" end="23.036">ful</span>
          <span begin="23.036" end="23.569">and</span>
        </p>
      </body>
    </tt>
    """
    lines, synced, rich = parse_ttml(ttml)
    assert rich is True
    assert lines[0].text == "Beautiful and"
    assert [word.word for word in lines[0].words] == ["Beauti", "ful", "and"]


def test_extract_developer_token_from_index_js():
    js = 'const t="eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abc_def-ghi";'
    html = '<script src="/assets/index~abc123.js"></script>'
    assert find_index_js_paths(html) == ["assets/index~abc123.js"]
    assert extract_developer_token(js).startswith("eyJ")
