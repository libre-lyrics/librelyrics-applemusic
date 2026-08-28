"""Apple Music lyrics module implementation.

Fetches lyrics from Apple Music's AMP API using user credentials.
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

import requests
from bs4 import BeautifulSoup

from librelyrics.exceptions import (ConfigurationError, LyricsNotFound,
                                    ProviderError)
from librelyrics.models import LyricsLine, LyricsResponse, LyricsWord
from librelyrics.modules.base import (LyricsModule, LyricsType,
                                      ModuleCapability, ModuleMeta)

logger = logging.getLogger('librelyrics.modules.applemusic')

AMP_API_URL = "https://amp-api.music.apple.com"
HOMEPAGE_URL = "https://music.apple.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:95.0) Gecko/20100101 Firefox/95.0"
)
INDEX_JS_RE = re.compile(r"/(assets/index[~-][^/\"]+\.js)")
JWT_RE = re.compile(
    r'"(eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)"'
)
APPLE_MUSIC_PATTERN = re.compile(
    r"https://music\.apple\.com/(\w{2})/(album|song)/.+?/(\d+)(\?i=(\d+))?"
)


def catalog_song_url(storefront: str, song_id: str) -> str:
    return f"{AMP_API_URL}/v1/catalog/{storefront}/songs/{song_id}"


def catalog_album_url(storefront: str, album_id: str) -> str:
    return f"{AMP_API_URL}/v1/catalog/{storefront}/albums/{album_id}"


def song_include_params() -> dict[str, str]:
    """AMP-only includes. ``lyrics`` on api.music.apple.com returns HTTP 400."""
    return {"include": "lyrics,albums"}


def find_index_js_paths(html: str) -> list[str]:
    return INDEX_JS_RE.findall(html)


def extract_developer_token(js_source: str) -> str:
    match = JWT_RE.search(js_source)
    if not match:
        raise ValueError("No Apple Music developer token found in JS assets")
    return match.group(1)


def parse_apple_music_url(url: str) -> dict[str, str | None]:
    match = APPLE_MUSIC_PATTERN.search(url)
    if not match:
        raise ValueError(f"Invalid Apple Music URL: {url}")

    region, kind, catalog_id, _track_flag, query_track_id = match.groups()
    if kind == "song":
        track_id = catalog_id
        album_id = catalog_id
    else:
        album_id = catalog_id
        track_id = query_track_id

    return {
        "region": region,
        "kind": kind,
        "album_id": album_id,
        "track_id": track_id,
    }


def parse_timestamp(timestamp: str | None) -> int:
    """Parse Apple Music TTML time values to milliseconds."""
    if not timestamp:
        return 0
    timestamp = timestamp.strip()
    try:
        if timestamp.endswith("s") and ":" not in timestamp:
            return int(float(timestamp[:-1]) * 1000)
        if ":" in timestamp:
            parts = timestamp.split(":")
            if len(parts) == 2:
                minutes = float(parts[0])
                seconds = float(parts[1])
                return int((minutes * 60 + seconds) * 1000)
            if len(parts) == 3:
                hours = float(parts[0])
                minutes = float(parts[1])
                seconds = float(parts[2])
                return int((hours * 3600 + minutes * 60 + seconds) * 1000)
            return 0
        return int(float(timestamp) * 1000)
    except (ValueError, IndexError):
        return 0


def parse_ttml(ttml: str) -> tuple[list[LyricsLine], bool, bool]:
    """Parse Apple Music TTML into lyrics lines.

    Returns:
        lines, is_synced, is_rich_synced
    """
    soup = BeautifulSoup(ttml, "lxml")
    timing = ""
    tt = soup.find("tt")
    if tt is not None:
        timing = tt.get("itunes:timing") or ""
    is_synced = timing != "None"
    is_rich = timing == "Word"

    lines: list[LyricsLine] = []
    for paragraph in soup.find_all("p"):
        spans = paragraph.find_all("span", recursive=False)
        text = " ".join(paragraph.get_text().split())
        start_ms = parse_timestamp(str(paragraph.get("begin"))) if is_synced else None
        end_ms = parse_timestamp(str(paragraph.get("end"))) if is_synced and paragraph.get("end") else None

        words = None
        if is_rich and spans:
            word_parts = []
            for span in spans:
                word_text = span.get_text()
                word_parts.append(
                    LyricsWord(
                        word=word_text,
                        start_ms=parse_timestamp(str(span.get("begin"))),
                        end_ms=parse_timestamp(str(span.get("end"))),
                    )
                )
            words = tuple(word_parts)
            text = " ".join(paragraph.get_text().split())

        if is_synced and start_ms is not None:
            lines.append(
                LyricsLine(text=text, start_ms=start_ms, end_ms=end_ms, words=words)
            )
        else:
            lines.append(LyricsLine(text=text))

    return lines, is_synced, is_rich


class AppleMusicModule(LyricsModule):
    """Apple Music lyrics provider module.

    Fetches lyrics from Apple Music's AMP API.
    Supports track and album URLs.
    """

    META: ClassVar[ModuleMeta] = ModuleMeta(
        name="Apple Music",
        regex=APPLE_MUSIC_PATTERN,
        requires_auth=True,
        description="Fetch lyrics from Apple Music",
        lyrics_types=frozenset({LyricsType.PLAIN, LyricsType.SYNCED, LyricsType.RICH_SYNCED}),
        capabilities=frozenset({
            ModuleCapability.SINGLE_TRACK,
            ModuleCapability.ALBUM,
        }),
        config_schema={
            "media_user_token": "Apple Music media-user-token cookie",
        },
    )
    LIBRELYRICS_API_VERSION: ClassVar[int] = 1
    _cached_developer_token: ClassVar[str | None] = None

    def __init__(self, url: str, config: dict) -> None:
        super().__init__(url, config)
        self._session: requests.Session | None = None
        self._account_storefront: str | None = None

    @classmethod
    def _fetch_developer_token(cls) -> str:
        """Fetch developer authorization token from Apple Music web player JS assets."""
        if cls._cached_developer_token:
            return cls._cached_developer_token

        headers = {"User-Agent": USER_AGENT}
        try:
            resp = requests.get(f"{HOMEPAGE_URL}/us/browse", headers=headers, timeout=10)
            resp.raise_for_status()
            for script_path in find_index_js_paths(resp.text):
                js_resp = requests.get(
                    f"{HOMEPAGE_URL}/{script_path}",
                    headers=headers,
                    timeout=10,
                )
                if js_resp.status_code != 200:
                    continue
                try:
                    token = extract_developer_token(js_resp.text)
                except ValueError:
                    continue
                cls._cached_developer_token = token
                logger.debug("Successfully extracted Apple Music developer token")
                return token
        except Exception as e:
            logger.warning(f"Failed to fetch developer token dynamically: {e}")

        raise ProviderError(
            "Failed to extract Apple Music developer authorization token from web assets."
        )

    def _ensure_session(self) -> None:
        """Ensure session is initialized with AMP auth headers."""
        media_user_token = self.config.get("media_user_token")

        if not media_user_token:
            raise ConfigurationError(
                "Apple Music requires 'media_user_token' in configuration. "
                "Run 'librelyrics config edit' to set it up."
            )

        developer_token = self._fetch_developer_token()
        if developer_token.startswith("Bearer "):
            developer_token = developer_token[len("Bearer "):]

        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "authorization": f"Bearer {developer_token}",
                "origin": HOMEPAGE_URL,
                "referer": f"{HOMEPAGE_URL}/",
                "accept": "application/json",
                "user-agent": USER_AGENT,
                "media-user-token": media_user_token,
            })
            self._session.cookies.set(
                "media-user-token",
                media_user_token,
                domain=".music.apple.com",
            )
            self._account_storefront = self._fetch_account_storefront()
            logger.debug(
                "Initialized Apple Music AMP session (storefront=%s)",
                self._account_storefront,
            )

    def _fetch_account_storefront(self) -> str | None:
        """Return the storefront tied to the Apple Music account, if available.

        Lyrics are licensed per-account storefront. A song URL from ``/in/`` can
        still 404 lyrics when the account is ``us`` (and vice versa).
        """
        try:
            resp = self.session.get(
                f"{AMP_API_URL}/v1/me/account",
                params={"meta": "subscription"},
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            storefront = (
                payload.get("meta", {})
                .get("subscription", {})
                .get("storefront")
            )
            if storefront:
                logger.debug("Using Apple Music account storefront: %s", storefront)
                return storefront
        except Exception as e:
            logger.warning("Could not determine Apple Music account storefront: %s", e)
        return None

    @property
    def session(self) -> requests.Session:
        """Get the Apple Music session."""
        if self._session is None:
            self._ensure_session()
        return self._session  # type: ignore

    @staticmethod
    def default_config() -> dict:
        """Return default Apple Music configuration."""
        return {
            "media_user_token": "",
        }

    @staticmethod
    def validate_config(config: dict) -> None:
        """Validate Apple Music configuration."""
        if not config.get("media_user_token"):
            raise ConfigurationError(
                "Apple Music requires 'media_user_token'. "
                "See README for instructions on finding it."
            )

    def _parse_url(self) -> dict:
        """Parse the Apple Music URL and extract components."""
        try:
            return parse_apple_music_url(self.url)
        except ValueError as e:
            raise LyricsNotFound(str(e)) from e

    def _storefronts_to_try(self, url_region: str) -> list[str]:
        storefronts: list[str] = []
        self.session
        if self._account_storefront:
            storefronts.append(self._account_storefront)
        if url_region and url_region not in storefronts:
            storefronts.append(url_region)
        return storefronts or [url_region or "us"]

    def fetch(self) -> LyricsResponse:
        """Fetch lyrics for the configured URL."""
        data = self._parse_url()

        if data["kind"] == "song" or data.get("track_id"):
            return self._fetch_track_lyrics(data["track_id"], data)

        tracks = self._get_album_tracks(data["album_id"], data["region"])
        if not tracks:
            raise LyricsNotFound("No tracks found in album")
        return self._fetch_track_lyrics(tracks[0], data)

    def fetch_album(self) -> list[LyricsResponse]:
        """Fetch lyrics for all tracks in an album."""
        data = self._parse_url()
        tracks = self._get_album_tracks(data["album_id"], data["region"])

        results = []
        for track_id in tracks:
            try:
                response = self._fetch_track_lyrics(track_id, data)
                results.append(response)
            except LyricsNotFound:
                logger.warning(f"No lyrics found for track: {track_id}")
            except Exception as e:
                logger.warning(f"Failed to fetch lyrics for track {track_id}: {e}")

        return results

    def _get_album_tracks(self, album_id: str, region: str) -> list[str]:
        """Get track IDs from an album."""
        last_error: Exception | None = None
        for storefront in self._storefronts_to_try(region):
            try:
                resp = self.session.get(
                    catalog_album_url(storefront, album_id),
                    timeout=10,
                )
                resp.raise_for_status()
                payload = resp.json()
                tracks = payload["data"][0]["relationships"]["tracks"]["data"]
                return [track["id"] for track in tracks]
            except Exception as e:
                last_error = e
                continue
        raise ProviderError(f"Failed to get album tracks: {last_error}") from last_error

    def get_album_info(self) -> dict:
        """Get album metadata."""
        data = self._parse_url()
        last_error: Exception | None = None
        for storefront in self._storefronts_to_try(data["region"]):
            try:
                resp = self.session.get(
                    catalog_album_url(storefront, data["album_id"]),
                    timeout=10,
                )
                resp.raise_for_status()
                album_data = resp.json()["data"][0]["attributes"]
                return {
                    "name": album_data.get("name", "Unknown Album"),
                    "artists": [{"name": album_data.get("artistName", "Unknown Artist")}],
                    "total_tracks": album_data.get("trackCount", 0),
                    "release_date": album_data.get("releaseDate", ""),
                }
            except Exception as e:
                last_error = e
                continue
        raise ProviderError(f"Failed to get album info: {last_error}") from last_error

    def _get_song(self, storefront: str, track_id: str) -> dict | None:
        resp = self.session.get(
            catalog_song_url(storefront, track_id),
            params=song_include_params(),
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("data"):
            return None
        return payload["data"][0]

    def _get_ttml(self, storefront: str, track_id: str, endpoint: str) -> str | None:
        resp = self.session.get(
            f"{catalog_song_url(storefront, track_id)}/{endpoint}",
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") or []
        if not data:
            return None
        return data[0].get("attributes", {}).get("ttml")

    def _lyrics_ttml_from_song(self, song: dict) -> str | None:
        lyrics = (
            song.get("relationships", {})
            .get("lyrics", {})
            .get("data") or []
        )
        if not lyrics:
            return None
        return lyrics[0].get("attributes", {}).get("ttml")

    def _fetch_track_lyrics(self, track_id: str, data: dict) -> LyricsResponse:
        """Fetch lyrics for a single track."""
        url_region = data.get("region") or "us"
        last_error: Exception | None = None
        song: dict | None = None
        storefront_used = url_region

        for storefront in self._storefronts_to_try(url_region):
            try:
                song = self._get_song(storefront, track_id)
            except Exception as e:
                last_error = e
                continue
            if song is not None:
                storefront_used = storefront
                break

        if song is None:
            raise ProviderError(
                f"Failed to get track metadata: {last_error or 'song not found'}"
            )

        track_data = song["attributes"]
        title = track_data["name"]
        artist = track_data["artistName"]
        album = track_data.get("albumName", "")
        track_number = track_data.get("trackNumber", 0)
        duration_ms = track_data.get("durationInMillis")

        ttml = self._get_ttml(storefront_used, track_id, "syllable-lyrics")
        if not ttml:
            ttml = self._lyrics_ttml_from_song(song)
        if not ttml:
            ttml = self._get_ttml(storefront_used, track_id, "lyrics")

        if not ttml:
            # Account storefront may lack lyrics even when the URL region has them.
            for storefront in self._storefronts_to_try(url_region):
                if storefront == storefront_used:
                    continue
                ttml = self._get_ttml(storefront, track_id, "syllable-lyrics")
                if ttml:
                    break
                ttml = self._get_ttml(storefront, track_id, "lyrics")
                if ttml:
                    break

        if not ttml:
            if not track_data.get("hasLyrics", False):
                raise LyricsNotFound(f"No lyrics available for: {title}")
            raise LyricsNotFound(
                f"No lyrics available for: {title} "
                f"(storefront={storefront_used})"
            )

        lines, is_synced, is_rich = parse_ttml(ttml)
        logger.debug("Fetched lyrics for: %s - %s", title, artist)

        return LyricsResponse(
            title=title,
            artist=artist,
            album=album,
            lyrics=lines,
            source=self.META.name,
            synced=is_synced,
            rich_synced=is_rich,
            duration_ms=duration_ms,
            metadata={
                "track_id": track_id,
                "track_number": track_number,
                "storefront": storefront_used,
            },
        )
