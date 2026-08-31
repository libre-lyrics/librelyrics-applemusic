"""Tests for the Apple Music plugin — API v2 compliance + helper functions."""
from __future__ import annotations

from librelyrics.models import TrackQuery
from librelyrics.modules.base import LIBRELYRICS_API_VERSION, ModuleCapability

from applemusic.module import (
    AMP_API_URL,
    AppleMusicModule,
    catalog_song_url,
    extract_developer_token,
    find_index_js_paths,
    parse_apple_music_url,
    parse_timestamp,
    parse_ttml,
    song_include_params,
)


# ---------------------------------------------------------------------------
# API v2 compliance
# ---------------------------------------------------------------------------


class TestAppleMusicApiV2:
    """Verify the plugin satisfies LibreLyrics API v2 requirements."""

    def test_api_version_is_2(self):
        assert AppleMusicModule.LIBRELYRICS_API_VERSION == LIBRELYRICS_API_VERSION == 2

    def test_meta_has_id(self):
        assert hasattr(AppleMusicModule.META, "id")
        assert AppleMusicModule.META.id == "applemusic"

    def test_meta_id_is_lowercase_alphanumeric(self):
        assert AppleMusicModule.META.id.isalnum()
        assert AppleMusicModule.META.id == AppleMusicModule.META.id.lower()

    def test_constructor_accepts_track_query(self):
        """v2 constructor must accept (query: TrackQuery, config: dict)."""
        query = TrackQuery(url="https://music.apple.com/us/song/test/123")
        module = AppleMusicModule(query=query, config={})
        assert module.query is query

    def test_url_property_delegates_to_query(self):
        query = TrackQuery(url="https://music.apple.com/in/song/perfect/1193701400")
        module = AppleMusicModule(query=query, config={})
        assert module.url == "https://music.apple.com/in/song/perfect/1193701400"

    def test_url_property_returns_none_when_no_url(self):
        query = TrackQuery(url=None)
        module = AppleMusicModule(query=query, config={})
        assert module.url is None

    def test_has_single_track_capability(self):
        assert AppleMusicModule.has_capability(ModuleCapability.SINGLE_TRACK)

    def test_has_album_capability(self):
        assert AppleMusicModule.has_capability(ModuleCapability.ALBUM)

    def test_has_resolve_capability(self):
        assert AppleMusicModule.has_capability(ModuleCapability.RESOLVE)

    def test_has_search_capability(self):
        assert AppleMusicModule.has_capability(ModuleCapability.SEARCH)

    def test_matches_artist_title_without_url(self):
        assert AppleMusicModule.matches(TrackQuery(artist="Ed Sheeran", title="Perfect")) is True

    def test_does_not_match_metadata_when_url_is_other_host(self):
        assert AppleMusicModule.matches(
            TrackQuery(url="https://open.spotify.com/track/abc", artist="A", title="T")
        ) is False

    def test_requires_auth(self):
        assert AppleMusicModule.META.requires_auth is True

    def test_matches_track_url(self):
        query = TrackQuery(url="https://music.apple.com/in/song/perfect/1193701400")
        assert AppleMusicModule.matches(query) is True

    def test_matches_album_url(self):
        query = TrackQuery(url="https://music.apple.com/us/album/divide/1193701079?i=1193701400")
        assert AppleMusicModule.matches(query) is True

    def test_does_not_match_unrelated_url(self):
        query = TrackQuery(url="https://open.spotify.com/track/abc")
        assert AppleMusicModule.matches(query) is False

    def test_default_config_has_media_user_token(self):
        cfg = AppleMusicModule.default_config()
        assert "media_user_token" in cfg


# ---------------------------------------------------------------------------
# Helper functions (pre-existing behaviour preserved)
# ---------------------------------------------------------------------------


def test_catalog_song_url_uses_amp_api():
    url = catalog_song_url("in", "1193701400")
    assert url.startswith(AMP_API_URL)
    assert url == "https://amp-api.music.apple.com/v1/catalog/in/songs/1193701400"


def test_song_include_params_requests_lyrics():
    params = song_include_params()
    assert params["include"] == "lyrics,albums"


def test_parse_song_url():
    data = parse_apple_music_url("https://music.apple.com/in/song/perfect/1193701400")
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


def test_parse_timestamp_bare_seconds():
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


def test_parse_word_ttml_keeps_syllable_fragments():
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
    assert [w.word for w in lines[0].words] == ["Beauti", "ful", "and"]


def test_extract_developer_token_from_index_js():
    js = 'const t="eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abc_def-ghi";'
    html = '<script src="/assets/index~abc123.js"></script>'
    assert find_index_js_paths(html) == ["assets/index~abc123.js"]
    assert extract_developer_token(js).startswith("eyJ")
