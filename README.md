# librelyrics-applemusic

Apple Music lyrics provider plugin for [LibreLyrics](https://github.com/libre-lyrics/librelyrics).

## Features

- Fetch plain, synced, and rich synced (word-level) lyrics from Apple Music
- Support for track and album URLs
- Karaoke-style Enhanced LRC output

## Installation

```bash
pip install librelyrics-applemusic
```

## Configuration

Requires `media_user_token` from Apple Music. Set it up via:

```bash
librelyrics config edit
```

### Getting your token

1. Open [Apple Music Web Player](https://music.apple.com) in your browser
2. Log in to your account
3. Open Developer Tools (F12) → Application/Storage → Cookies
4. Copy the `media-user-token` cookie value (`media_user_token`)

## Supported URLs

- `https://music.apple.com/<region>/song/<name>/<id>`
- `https://music.apple.com/<region>/album/<name>/<id>?i=<track_id>`
- `https://music.apple.com/<region>/album/<name>/<id>`

Lyrics are fetched from Apple Music's AMP API using your account storefront
(not only the country code in the URL). A `/in/song/...` link still works if
your Apple Music account is in another country.

## Usage

Once installed, the plugin is automatically discovered by LibreLyrics:

```bash
librelyrics "https://music.apple.com/us/album/some-album/123456789?i=987654321"
```

## License

GPL-3.0-or-later
