import asyncio
import datetime
import re
from typing import AsyncGenerator, Callable
from xml.etree import ElementTree

import m3u8
import structlog

from .base import AppleMusicBaseInterface
from .constants import DRM_DEFAULT_KEY_MAPPING, MP4_FORMAT_CODECS, SONG_CODEC_REGEX_MAP
from .enums import SongCodec, SyncedLyricsFormat
from .exceptions import (
    GamdlInterfaceDecryptionNotAvailableError,
    GamdlInterfaceFormatNotAvailableError,
    GamdlInterfaceMediaNotStreamableError,
)
from .types import (
    AppleMusicMedia,
    DecryptionKeyAv,
    Lyrics,
    MediaFileFormat,
    StreamInfo,
    StreamInfoAv,
)

logger = structlog.get_logger(__name__)


class AppleMusicSongInterface:
    def __init__(
        self,
        base: AppleMusicBaseInterface,
        synced_lyrics_format: (
            list[SyncedLyricsFormat] | SyncedLyricsFormat
        ) = [SyncedLyricsFormat.LRC],
        codec_priority: list[SongCodec] = [SongCodec.AAC_WEB],
        use_album_date: bool = False,
        skip_stream_info: bool = False,
        ask_codec_function: Callable[[list[dict]], dict | None] | None = None,
    ):
        self.base = base
        if isinstance(synced_lyrics_format, list):
            self.synced_lyrics_formats = synced_lyrics_format
            self.synced_lyrics_format = (
                synced_lyrics_format[0]
                if synced_lyrics_format
                else SyncedLyricsFormat.LRC
            )
        else:
            self.synced_lyrics_formats = [synced_lyrics_format]
            self.synced_lyrics_format = synced_lyrics_format
        self.codec_priority = codec_priority
        self.use_album_date = use_album_date
        self.skip_stream_info = skip_stream_info
        self.ask_codec_function = ask_codec_function

    async def get_lyrics(
        self,
        song_metadata: dict,
    ) -> Lyrics | None:
        log = logger.bind(
            action="get_lyrics",
            song_id=song_metadata["id"],
        )

        if song_metadata["attributes"]["playParams"].get("isLibrary"):
            log.debug("library_song_no_lyrics")
            return None

        if not song_metadata["attributes"]["hasLyrics"]:
            log.debug("no_lyrics")
            return None

        if (
            "relationships" not in song_metadata
            or "syllable-lyrics" not in song_metadata["relationships"]
        ):
            song_metadata = (
                await self.base.apple_music_api.get_song(
                    song_metadata["id"],
                )
            )["data"][0]

        if (
            "syllable-lyrics" in song_metadata["relationships"]
            and "data" in song_metadata["relationships"]["syllable-lyrics"]
            and len(song_metadata["relationships"]["syllable-lyrics"]["data"]) > 0
            and "attributes" in song_metadata["relationships"]["syllable-lyrics"]["data"][0]
            and song_metadata["relationships"]["syllable-lyrics"]["data"][0]["attributes"].get(
                "ttml"
            )
            is not None
        ):
            lyrics = self._get_lyrics(
                song_metadata["relationships"]["syllable-lyrics"]["data"][0]["attributes"][
                    "ttml"
                ],
            )

            log.debug("success", lyrics=lyrics)

            return lyrics
        else:
            log.debug("no_lyrics_data")

    def _get_lyrics(
        self,
        lyrics_ttml: str,
    ) -> Lyrics:
        lyrics_ttml_et = ElementTree.fromstring(lyrics_ttml)
        unsynced_lyrics = []
        synced_lines: dict[SyncedLyricsFormat, list[str]] = {
            fmt: [] for fmt in self.synced_lyrics_formats
        }
        index = 1

        for div in lyrics_ttml_et.iter("{http://www.w3.org/ns/ttml}div"):
            stanza = []
            unsynced_lyrics.append(stanza)

            for p in div.iter("{http://www.w3.org/ns/ttml}p"):
                text = "".join(p.itertext())
                if text:
                    stanza.append(text)

                if p.attrib.get("begin"):
                    if SyncedLyricsFormat.ELRC in self.synced_lyrics_formats:
                        synced_lines[SyncedLyricsFormat.ELRC].append(
                            self._get_lyrics_line_elrc(p)
                        )

                    if SyncedLyricsFormat.LRC in self.synced_lyrics_formats:
                        synced_lines[SyncedLyricsFormat.LRC].append(
                            self._get_lyrics_line_lrc(p)
                        )

                    if SyncedLyricsFormat.SRT in self.synced_lyrics_formats:
                        synced_lines[SyncedLyricsFormat.SRT].append(
                            self._get_lyrics_line_srt(index, p)
                        )

                    index += 1

        synced_by_format: dict[SyncedLyricsFormat, str] = {}
        for fmt in self.synced_lyrics_formats:
            if fmt == SyncedLyricsFormat.TTML:
                synced_by_format[fmt] = lyrics_ttml
            else:
                lines = synced_lines.get(fmt, [])
                synced_by_format[fmt] = (
                    "\n".join(lines + ["\n"]) if lines else None
                )

        primary_synced = synced_by_format.get(self.synced_lyrics_format)

        return Lyrics(
            synced=primary_synced,
            unsynced=(
                "\n\n".join(["\n".join(lyric_group) for lyric_group in unsynced_lyrics])
                if unsynced_lyrics
                else None
            ),
            synced_by_format=synced_by_format,
        )

    def _parse_ttml_timestamp(
        self,
        timestamp_ttml: str,
    ) -> datetime.datetime:
        mins_secs_ms = re.findall(r"\d+", timestamp_ttml)
        ms, secs, mins = 0, 0, 0

        if len(mins_secs_ms) == 2 and ":" in timestamp_ttml:
            secs, mins = int(mins_secs_ms[-1]), int(mins_secs_ms[-2])

        elif len(mins_secs_ms) == 1:
            ms = int(mins_secs_ms[-1])

        else:
            secs = float(f"{mins_secs_ms[-2]}.{mins_secs_ms[-1]}")
            if len(mins_secs_ms) > 2:
                mins = int(mins_secs_ms[-3])

        return datetime.datetime.fromtimestamp(
            (mins * 60) + secs + (ms / 1000),
            tz=datetime.timezone.utc,
        )

    def _get_lyrics_line_srt(self, index: int, element: ElementTree.Element) -> str:
        timestamp_begin_ttml = element.attrib.get("begin")
        timestamp_end_ttml = element.attrib.get("end")
        text = "".join(element.itertext())

        timestamp_begin = self._parse_ttml_timestamp(timestamp_begin_ttml)
        timestamp_end = self._parse_ttml_timestamp(timestamp_end_ttml)

        return (
            f"{index}\n"
            f"{timestamp_begin.strftime('%H:%M:%S,%f')[:-3]} --> "
            f"{timestamp_end.strftime('%H:%M:%S,%f')[:-3]}\n"
            f"{text}\n"
        )

    def _format_lrc_timestamp(self, timestamp_ttml: str) -> str:
        timestamp = self._parse_ttml_timestamp(timestamp_ttml)
        ms_new = timestamp.strftime("%f")[:-3]

        if int(ms_new[-1]) >= 5:
            ms = int(f"{int(ms_new[:2]) + 1}") * 10
            timestamp += datetime.timedelta(milliseconds=ms) - datetime.timedelta(
                microseconds=timestamp.microsecond
            )

        return timestamp.strftime("%M:%S.%f")[:-4]

    def _get_lyrics_line_elrc(self, element: ElementTree.Element) -> str:
        timestamp_ttml = element.attrib.get("begin")
        timestamp_str = self._format_lrc_timestamp(timestamp_ttml)
        spans = element.findall("{http://www.w3.org/ns/ttml}span")
        if not spans:
            return f"[{timestamp_str}]" + "".join(element.itertext())

        words = []
        if element.text:
            words.append(element.text)
        for span in spans:
            span_begin = span.attrib.get("begin")
            span_time = self._format_lrc_timestamp(span_begin) if span_begin else ""
            span_text = span.text or ""
            span_tail = span.tail or ""
            if span_time:
                words.append(f"<{span_time}>{span_text}{span_tail}")
            else:
                words.append(f"{span_text}{span_tail}")

        return f"[{timestamp_str}]{''.join(words)}"

    def _get_lyrics_line_lrc(self, element: ElementTree.Element) -> str:
        timestamp_ttml = element.attrib.get("begin")
        text = "".join(element.itertext())

        timestamp = self._parse_ttml_timestamp(timestamp_ttml)
        ms_new = timestamp.strftime("%f")[:-3]

        if int(ms_new[-1]) >= 5:
            ms = int(f"{int(ms_new[:2]) + 1}") * 10
            timestamp += datetime.timedelta(milliseconds=ms) - datetime.timedelta(
                microseconds=timestamp.microsecond
            )

        return f"[{timestamp.strftime('%M:%S.%f')[:-4]}]{text}"

    def _switch_m3u8_master_url_to_default(self, m3u8_master_url: str) -> str:
        return re.sub(
            r"(P\d+)_[^/]+(\.m3u8)",
            r"\1_default\2",
            m3u8_master_url,
        )

    def _get_m3u8_from_playback(self, playback: dict) -> str | None:
        log = logger.bind(action="get_m3u8_master_url_from_playback")

        m3u8_master_url = playback["songList"][0].get("hls-playlist-url")

        if m3u8_master_url:
            m3u8_master_url = self._switch_m3u8_master_url_to_default(m3u8_master_url)
            log.debug("success", m3u8_master_url=m3u8_master_url)
            return m3u8_master_url

        log.debug("no_m3u8_master_url")

    async def _get_m3u8_master_url_from_assets(
        self,
        media_id: str,
    ) -> str | None:
        log = logger.bind(
            action="get_m3u8_master_url_from_assets",
            song_id=media_id,
        )

        assets = await self.base.apple_music_api.get_assets(
            media_id,
            "song",
        )

        asset = next(
            (
                asset
                for asset in assets.get("results", {}).get("assets", [])
                if asset.get("url")
            ),
            None,
        )
        enhanced = asset["url"] if asset else None

        if enhanced:
            enhanced = self._switch_m3u8_master_url_to_default(enhanced)
            log.debug("success", m3u8_master_url=enhanced)
            return enhanced

        log.debug("no_m3u8_master_url")

        return None

    async def _get_m3u8_master_url(
        self,
        media_id: str,
        playback: dict | None,
    ) -> str | None:
        if playback:
            m3u8_master_url = self._get_m3u8_from_playback(playback)
            if m3u8_master_url:
                return m3u8_master_url

        return await self._get_m3u8_master_url_from_assets(media_id)

    async def get_stream_info(
        self,
        media_id: str,
        is_library: bool,
        webplayback: dict | None = None,
        playback: dict | None = None,
    ) -> StreamInfoAv:
        stream_info = None

        if is_library:
            stream_info = await self._get_library_stream_info(webplayback)
        else:
            m3u8_master_url = None
            fetched_m3u8_master_url = False

            for codec in self.codec_priority:
                if codec.is_web:
                    stream_info = await self._get_web_stream_info(webplayback, codec)
                else:
                    if not fetched_m3u8_master_url:
                        m3u8_master_url = await self._get_m3u8_master_url(
                            media_id,
                            playback,
                        )
                        fetched_m3u8_master_url = True

                    stream_info = await self._get_stream_info_nonweb(
                        m3u8_master_url,
                        codec,
                    )

                if stream_info:
                    break

        if not stream_info:
            raise GamdlInterfaceFormatNotAvailableError(
                media_id=media_id,
                codec=[codec.value for codec in self.codec_priority],
            )

        return stream_info

    async def _get_stream_info_nonweb(
        self,
        m3u8_master_url: str | None,
        codec: SongCodec,
    ) -> StreamInfoAv | None:
        log = logger.bind(action="get_song_stream_info")

        if not m3u8_master_url:
            log.debug("no_m3u8_master_url")
            return None

        m3u8_master_obj = m3u8.loads(
            (await self.base.get_response(m3u8_master_url)).text
        )
        m3u8_master_data = m3u8_master_obj.data
        is_enhanced = self._is_enhanced_m3u8_master(m3u8_master_data)

        if is_enhanced:
            stream_info = await self._get_stream_info_enhanced(
                m3u8_master_url,
                m3u8_master_data,
                codec,
            )
        else:
            stream_info = await self._get_stream_info_nonenhanced(
                m3u8_master_url,
                m3u8_master_data,
                codec,
            )

        if stream_info:
            log.debug(
                "success",
                stream_info=stream_info,
                is_enhanced=is_enhanced,
            )

        return stream_info

    def _is_enhanced_m3u8_master(self, m3u8_master_data: dict) -> bool:
        return any(
            playlist.get("stream_info", {}).get("audio")
            for playlist in m3u8_master_data.get("playlists", [])
        )

    async def _get_stream_info_enhanced(
        self,
        m3u8_master_url: str,
        m3u8_master_data: dict,
        codec: SongCodec,
    ) -> StreamInfoAv | None:
        log = logger.bind(action="get_song_stream_info_enhanced")

        if codec == SongCodec.ASK:
            playlist = await self._get_playlist_from_user(m3u8_master_data)
        else:
            playlist = self._get_playlist_from_codec_enhanced(
                m3u8_master_data,
                codec,
            )

        if playlist is None:
            log.debug("no_matching_playlist", codec=codec.value)
            return None

        stream_info = await self._get_stream_info_from_playlist(
            m3u8_master_url,
            playlist,
        )

        log.debug("success", stream_info=stream_info)

        return stream_info

    async def _get_stream_info_nonenhanced(
        self,
        m3u8_master_url: str,
        m3u8_master_data: dict,
        codec: SongCodec,
    ) -> StreamInfoAv | None:
        log = logger.bind(action="get_song_stream_info_nonenhanced")

        if codec == SongCodec.ASK:
            playlist = await self._get_playlist_from_user(m3u8_master_data)
        else:
            playlist = self._get_playlist_from_codec_nonenhanced(
                m3u8_master_data,
                codec,
            )

        if playlist is None:
            log.debug("no_matching_playlist", codec=codec.value)
            return None

        stream_info = await self._get_stream_info_from_playlist(
            m3u8_master_url,
            playlist,
            True,
        )

        log.debug("success", stream_info=stream_info)

        return stream_info

    async def _get_stream_info_from_playlist(
        self,
        m3u8_master_url: str,
        playlist: dict,
        use_single_content_key: bool = False,
    ) -> StreamInfoAv:
        stream_info = StreamInfo(use_single_content_key=use_single_content_key)
        stream_info.stream_url = (
            f"{m3u8_master_url.rpartition('/')[0]}/{playlist['uri']}"
        )
        stream_info.codec = playlist["stream_info"]["codecs"]
        is_mp4 = any(stream_info.codec.startswith(codec) for codec in MP4_FORMAT_CODECS)

        m3u8_obj = m3u8.loads(
            (await self.base.get_response(stream_info.stream_url)).text
        )

        stream_info.widevine_pssh = self._get_drm_uri_from_m3u8_keys(
            m3u8_obj,
            "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",
        )
        stream_info.playready_pssh = self._get_drm_uri_from_m3u8_keys(
            m3u8_obj,
            "com.microsoft.playready",
        )
        stream_info.fairplay_key = self._get_drm_uri_from_m3u8_keys(
            m3u8_obj,
            "com.apple.streamingkeydelivery",
        )

        stream_info_av = StreamInfoAv(
            audio_track=stream_info,
            file_format=MediaFileFormat.MP4 if is_mp4 else MediaFileFormat.M4A,
        )

        return stream_info_av

    def _get_playlist_from_codec_enhanced(
        self, m3u8_data: dict, codec: SongCodec
    ) -> dict | None:
        matching_playlists = [
            playlist
            for playlist in m3u8_data["playlists"]
            if re.fullmatch(
                SONG_CODEC_REGEX_MAP[codec.value], playlist["stream_info"]["audio"]
            )
        ]

        if not matching_playlists:
            return None

        return max(
            matching_playlists,
            key=lambda x: x["stream_info"]["average_bandwidth"],
        )

    def _get_playlist_from_codec_nonenhanced(
        self, m3u8_data: dict, codec: SongCodec
    ) -> dict | None:
        codec_values = {
            SongCodec.AAC: {"mp4a.40.2"},
            SongCodec.AAC_HE: {"mp4a.40.5"},
        }.get(codec)
        if not codec_values:
            return None

        matching_playlists = [
            playlist
            for playlist in m3u8_data["playlists"]
            if playlist["stream_info"].get("codecs") in codec_values
        ]

        if not matching_playlists:
            return None

        return max(
            matching_playlists,
            key=lambda x: x["stream_info"]["average_bandwidth"],
        )

    async def _get_playlist_from_user(self, m3u8_data: dict) -> dict | None:
        if self.ask_codec_function:
            playlist = self.ask_codec_function(
                [playlist for playlist in m3u8_data["playlists"]]
            )
            if asyncio.iscoroutine(playlist):
                playlist = await playlist

            return playlist

        return None

    def _get_drm_uri_from_m3u8_keys(
        self,
        m3u8_obj: m3u8.M3U8,
        drm_key: str,
    ) -> str | None:
        default_uri = DRM_DEFAULT_KEY_MAPPING[drm_key]

        for key in m3u8_obj.keys:
            if key.keyformat == drm_key and key.uri != default_uri:
                return key.uri
        return None

    async def _get_web_stream_info(
        self,
        webplayback: dict | None,
        codec: SongCodec,
    ) -> StreamInfoAv:
        log = logger.bind(action="get_web_song_stream_info")

        if not webplayback:
            log.debug("no_webplayback")
            return None

        flavor = codec.flavor

        stream_info = StreamInfo(
            use_cenc=codec.is_cenc,
        )
        asset = next(
            (i for i in webplayback["songList"][0]["assets"] if i["flavor"] == flavor),
            None,
        )
        if not asset:
            log.debug("no_matching_asset", codec=codec.value, flavor=flavor)
            return None

        stream_info.stream_url = asset["URL"]

        m3u8_obj = m3u8.loads(
            (await self.base.get_response(stream_info.stream_url)).text
        )

        if stream_info.use_cenc:
            stream_info.widevine_pssh = m3u8_obj.keys[0].uri
        else:
            stream_info.fairplay_key = m3u8_obj.keys[0].uri

        stream_info_av = StreamInfoAv(
            media_id=webplayback["songList"][0]["songId"],
            audio_track=stream_info,
            file_format=MediaFileFormat.M4A,
        )
        log.debug("success", stream_info=stream_info_av)

        return stream_info_av

    async def _get_library_stream_info(
        self,
        webplayback: dict | None,
    ) -> StreamInfoAv | None:
        log = logger.bind(action="get_library_song_stream_info")

        if not webplayback:
            log.debug("no_webplayback")
            return None

        stream_info = StreamInfo(drm_free=True)

        if len(webplayback["songList"][0]["assets"]) == 0:
            log.debug("no_matching_asset")
            return None
        asset = webplayback["songList"][0]["assets"][0]

        stream_info.stream_url = asset["URL"]

        stream_info_av = StreamInfoAv(
            media_id=webplayback["songList"][0]["songId"],
            audio_track=stream_info,
            file_format=MediaFileFormat.M4A,
        )
        log.debug("success", stream_info=stream_info_av)

        return stream_info_av

    async def get_media(
        self,
        media: AppleMusicMedia,
    ) -> AsyncGenerator[AppleMusicMedia, None]:
        if not media.media_metadata:
            media.media_metadata = (
                await (
                    self.base.apple_music_api.get_library_song(media.media_id)
                    if media.is_library
                    else self.base.apple_music_api.get_song(media.media_id)
                )
            )["data"][0]

        if media.media_metadata["attributes"].get("playParams", {}).get("isLibrary"):
            catalog_metadata = self.base.get_catalog_metadata_from_library(
                media.media_metadata
            )
            if catalog_metadata:
                media.media_id = catalog_metadata["id"]
                media.is_library = False
                media.media_metadata = catalog_metadata

        yield media

        if not self.base.is_media_streamable(media.media_metadata):
            raise GamdlInterfaceMediaNotStreamableError(
                media_id=media.media_id,
            )

        if media.playlist_metadata:
            media.playlist_tags = self.base.get_playlist_tags(
                media.playlist_metadata,
                media.index,
            )

        media.cover = await self.base.get_cover(media.media_metadata)

        media.lyrics = await self.get_lyrics(media.media_metadata)

        if self.base.wrapper_api:
            playback = (
                await self.base.wrapper_api.get_playback(media.media_id)
                if not media.is_library
                else None
            )
            webplayback = (
                await self.base.apple_music_api.get_webplayback(
                    media.media_id,
                    media.is_library,
                )
                if media.is_library
                or any(codec.is_web for codec in self.codec_priority)
                else None
            )
        else:
            playback = None
            webplayback = await self.base.apple_music_api.get_webplayback(
                media.media_id,
                media.is_library,
            )

        if playback:
            media.tags = await self.base.get_tags_from_asset_info(
                playback["songList"][0]["assets"][0]["metadata"],
                media.lyrics.unsynced if media.lyrics else None,
                self.use_album_date,
            )
        else:
            media.tags = await self.base.get_tags_from_asset_info(
                webplayback["songList"][0]["assets"][0]["metadata"],
                media.lyrics.unsynced if media.lyrics else None,
                self.use_album_date,
            )

        media.tags.isrc = media.media_metadata["attributes"].get("isrc")
        albums = (
            media.media_metadata.get("relationships", {})
            .get("albums", {})
            .get("data", [])
        )
        if albums:
            album_attributes = albums[0]["attributes"]
            media.tags.upc = album_attributes.get("upc")
            media.tags.record_label = album_attributes.get("recordLabel")
            media.tags.release_date = album_attributes.get("releaseDate")
            if album_attributes.get("artistName"):
                media.tags.album_artist = album_attributes["artistName"]
            album_data = await self.base.get_album_cached(albums[0]["id"])
            if album_data:
                album_artists = (
                    album_data.get("relationships", {})
                    .get("artists", {})
                    .get("data", [])
                )
                if album_artists:
                    media.tags.album_artists = [
                        a["attributes"]["name"]
                        for a in album_artists
                        if "attributes" in a and "name" in a["attributes"]
                    ]

        artists = (
            media.media_metadata.get("relationships", {})
            .get("artists", {})
            .get("data", [])
        )
        if artists:
            media.tags.artists = [
                a["attributes"]["name"]
                for a in artists
                if "attributes" in a and "name" in a["attributes"]
            ]

        credits = (
            media.media_metadata.get("relationships", {})
            .get("credits", {})
            .get("data", [])
        )
        if credits:
            composers = []
            for credit_group in credits:
                if (
                    credit_group.get("attributes", {}).get("kind")
                    == "composer-and-lyrics"
                ):
                    credit_artists = (
                        credit_group.get("relationships", {})
                        .get("credit-artists", {})
                        .get("data", [])
                    )
                    for artist in credit_artists:
                        artist_name = artist.get("attributes", {}).get("name")
                        if artist_name and artist_name not in composers:
                            composers.append(artist_name)
            if composers:
                media.tags.composer = composers

        if not self.skip_stream_info:
            media.stream_info = await self.get_stream_info(
                media.media_id,
                media.is_library,
                webplayback,
                playback,
            )

            if media.stream_info.audio_track.drm_free:
                pass
            elif (
                not self.base.wrapper_api
                and not media.stream_info.audio_track.widevine_pssh
            ) or (
                self.base.wrapper_api
                and not media.stream_info.audio_track.fairplay_key
                and not media.stream_info.audio_track.use_cenc
            ):
                raise GamdlInterfaceDecryptionNotAvailableError(media_id=media.media_id)
            elif media.stream_info.audio_track.widevine_pssh:
                media.decryption_key = DecryptionKeyAv(
                    audio_track=await self.base.get_decryption_key(
                        media.stream_info.audio_track.widevine_pssh,
                        media.media_id,
                    )
                )

        media.partial = False

        yield media
