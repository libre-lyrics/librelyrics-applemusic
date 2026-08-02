"""Apple Music lyrics module implementation.

Fetches lyrics from Apple Music's API using user credentials.
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

import requests
from bs4 import BeautifulSoup

from librelyrics.exceptions import (ConfigurationError, LyricsNotFound,
                                    ProviderError)
from librelyrics.models import LyricsLine, LyricsResponse
from librelyrics.modules.base import (LyricsModule, LyricsType,
                                      ModuleCapability, ModuleMeta)

logger = logging.getLogger('librelyrics.modules.applemusic')

# URL pattern for Apple Music
APPLE_MUSIC_PATTERN = re.compile(
    r"https://music\.apple\.com/(\w{2})/(album|song)/.+?/(\d+)(\?i=(\d+))?"
)


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
            'media_user_token': 'Apple Music media-user-token cookie',
        },
    )
    LIBRELYRICS_API_VERSION: ClassVar[int] = 1
    _cached_developer_token: ClassVar[str | None] = None
    
    def __init__(self, url: str, config: dict) -> None:
        super().__init__(url, config)
        self._session: requests.Session | None = None
    
    @classmethod
    def _fetch_developer_token(cls) -> str:
        """Fetch developer authorization token from Apple Music web player JS assets."""
        if cls._cached_developer_token:
            return cls._cached_developer_token
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:95.0) Gecko/20100101 Firefox/95.0",
        }
        try:
            resp = requests.get("https://music.apple.com/us/browse", headers=headers, timeout=10)
            resp.raise_for_status()
            
            scripts = re.findall(r'src="(/assets/index~[^"]+\.js)"', resp.text)
            if not scripts:
                scripts = re.findall(r'src="(/assets/index[^"]+\.js)"', resp.text)
            
            for script_path in scripts:
                js_url = f"https://music.apple.com{script_path}"
                js_resp = requests.get(js_url, headers=headers, timeout=10)
                if js_resp.status_code == 200:
                    tokens = re.findall(r'eyJhbGciOiJFUzI1NiI[^\'\"]+|eyJ0eXAiOiJKV1Qi[^\'\"]+', js_resp.text)
                    if tokens:
                        token = tokens[0]
                        if not token.startswith("Bearer "):
                            token = f"Bearer {token}"
                        cls._cached_developer_token = token
                        logger.debug("Successfully extracted Apple Music developer token")
                        return token
        except Exception as e:
            logger.warning(f"Failed to fetch developer token dynamically: {e}")
        
        raise ProviderError("Failed to extract Apple Music developer authorization token from web assets.")

    def _ensure_session(self) -> None:
        """Ensure session is initialized with auth headers."""
        media_user_token = self.config.get('media_user_token')
        
        if not media_user_token:
            raise ConfigurationError(
                "Apple Music requires 'media_user_token' in configuration. "
                "Run 'librelyrics config edit' to set it up."
            )
        
        auth_bearer = self._fetch_developer_token()
        
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "authorization": auth_bearer,
                "media-user-token": media_user_token,
                "Origin": "https://music.apple.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:95.0) Gecko/20100101 Firefox/95.0",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://music.apple.com/",
                "content-type": "application/json",
                "x-apple-renewal": "true",
                "DNT": "1",
                "Connection": "keep-alive",
                "l": "en-US",
            })
            logger.debug("Initialized Apple Music session")
    
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
            'media_user_token': '',
        }
    
    @staticmethod
    def validate_config(config: dict) -> None:
        """Validate Apple Music configuration."""
        if not config.get('media_user_token'):
            raise ConfigurationError(
                "Apple Music requires 'media_user_token'. "
                "See README for instructions on finding it."
            )
    
    def _parse_url(self) -> dict:
        """Parse the Apple Music URL and extract components."""
        match = APPLE_MUSIC_PATTERN.search(self.url)
        if not match:
            raise LyricsNotFound(f"Invalid Apple Music URL: {self.url}")
        
        region, kind, album_id, track_flag, track_id = match.groups()
        return {
            'region': region,
            'kind': kind,
            'album_id': album_id,
            'track_flag': track_flag,
            'track_id': track_id,
        }
    
    def fetch(self) -> LyricsResponse:
        """Fetch lyrics for the configured URL."""
        data = self._parse_url()
        
        if data['track_flag']:
            # URL has ?i=trackid parameter
            return self._fetch_track_lyrics(data['track_id'], data)
        elif data['kind'] == 'song':
            # Direct song URL
            return self._fetch_track_lyrics(data['album_id'], data)
        else:
            # Album URL - fetch first track
            tracks = self._get_album_tracks(data['album_id'], data['region'])
            if not tracks:
                raise LyricsNotFound("No tracks found in album")
            return self._fetch_track_lyrics(tracks[0], data)
    
    def fetch_album(self) -> list[LyricsResponse]:
        """Fetch lyrics for all tracks in an album."""
        data = self._parse_url()
        tracks = self._get_album_tracks(data['album_id'], data['region'])
        
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
        try:
            resp = self.session.get(
                f"https://api.music.apple.com/v1/catalog/{region}/albums/{album_id}",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            
            tracks = data['data'][0]['relationships']['tracks']['data']
            return [track['id'] for track in tracks]
        except Exception as e:
            raise ProviderError(f"Failed to get album tracks: {e}") from e
    
    def get_album_info(self) -> dict:
        """Get album metadata."""
        data = self._parse_url()
        try:
            resp = self.session.get(
                f"https://api.music.apple.com/v1/catalog/{data['region']}/albums/{data['album_id']}",
                timeout=10,
            )
            resp.raise_for_status()
            album_data = resp.json()['data'][0]['attributes']
            
            return {
                'name': album_data.get('name', 'Unknown Album'),
                'artists': [{'name': album_data.get('artistName', 'Unknown Artist')}],
                'total_tracks': album_data.get('trackCount', 0),
                'release_date': album_data.get('releaseDate', ''),
            }
        except Exception as e:
            raise ProviderError(f"Failed to get album info: {e}") from e
    
    def _fetch_track_lyrics(self, track_id: str, data: dict) -> LyricsResponse:
        """Fetch lyrics for a single track."""
        region = data.get('region', 'us')
        
        # Get track metadata
        try:
            resp = self.session.get(
                f"https://api.music.apple.com/v1/catalog/{region}/songs/{track_id}",
                timeout=10,
            )
            resp.raise_for_status()
            track_data = resp.json()['data'][0]['attributes']
        except Exception as e:
            raise ProviderError(f"Failed to get track metadata: {e}") from e
        
        title = track_data['name']
        artist = track_data['artistName']
        album = track_data.get('albumName', '')
        track_number = track_data.get('trackNumber', 0)
        
        if not track_data.get('hasLyrics', False):
            raise LyricsNotFound(f"No lyrics available for: {title}")
        
        # Get lyrics
        try:
            resp = self.session.get(
                f"https://amp-api.music.apple.com/v1/catalog/{region}/songs/{track_id}/lyrics",
                timeout=10,
            )
            resp.raise_for_status()
            lyrics_data = resp.json()
        except Exception as e:
            raise ProviderError(f"Failed to get lyrics: {e}") from e
        
        # Parse TTML lyrics
        ttml = lyrics_data['data'][0]['attributes']['ttml']
        soup = BeautifulSoup(ttml, 'lxml')
        paragraphs = soup.find_all("p")
        
        lines: list[LyricsLine] = []
        is_synced = 'itunes:timing="None"' not in ttml
        
        for paragraph in paragraphs:
            text = paragraph.text
            
            if is_synced:
                begin = paragraph.get('begin', '')
                if begin:
                    start_ms = self._parse_timestamp(begin)
                    lines.append(LyricsLine(text=text, start_ms=start_ms))
                else:
                    lines.append(LyricsLine(text=text))
            else:
                lines.append(LyricsLine(text=text))
        
        logger.debug(f"Fetched lyrics for: {title} - {artist}")
        
        return LyricsResponse(
            title=title,
            artist=artist,
            album=album,
            lyrics=lines,
            source=self.META.name,
            synced=is_synced,
            metadata={
                'track_id': track_id,
                'track_number': track_number,
            },
        )
    
    def _parse_timestamp(self, timestamp: str) -> int:
        """Parse Apple Music timestamp to milliseconds."""
        try:
            if 's' in timestamp and ':' not in timestamp:
                # Format: "123.456s"
                seconds = float(timestamp.rstrip('s'))
                return int(seconds * 1000)
            elif ':' in timestamp:
                # Format: "1:23.456" or "01:23.456"
                parts = timestamp.split(':')
                if len(parts) == 2:
                    minutes = float(parts[0])
                    seconds = float(parts[1])
                    return int((minutes * 60 + seconds) * 1000)
                elif len(parts) == 3:
                    # Format: "0:01:23.456"
                    minutes = float(parts[1])
                    seconds = float(parts[2])
                    return int((minutes * 60 + seconds) * 1000)
            return 0
        except (ValueError, IndexError):
            return 0
