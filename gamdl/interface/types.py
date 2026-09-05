import datetime
from dataclasses import dataclass
from typing import Any

from .enums import MediaFileFormat, MediaRating, MediaType, SyncedLyricsFormat


@dataclass
class Lyrics:
    synced: str = None
    unsynced: str = None
    synced_by_format: dict[SyncedLyricsFormat, str] = None


@dataclass
class MediaTags:
    album: str = None
    album_artist: str = None
    album_artists: list[str] = None
    album_id: int = None
    album_sort: str = None
    artist: str = None
    artist_id: int = None
    artist_sort: str = None
    artists: list[str] = None
    comment: str = None
    compilation: bool = None
    composer: str | list[str] = None
    composer_id: int = None
    composer_sort: str = None
    copyright: str = None
    date: datetime.date | str = None
    disc: int = None
    disc_total: int = None
    gapless: bool = None
    genre: str = None
    genre_id: int = None
    isrc: str = None
    lyrics: str = None
    media_type: MediaType = None
    rating: MediaRating = None
    record_label: str = None
    release_date: str = None
    storefront: str = None
    title: str = None
    title_id: int = None
    title_sort: str = None
    track: int = None
    track_total: int = None
    upc: str = None
    xid: str = None

    def as_mp4_tags(self, date_format: str = None) -> dict:
        disc_mp4 = [
            self.disc if self.disc is not None else 0,
            self.disc_total if self.disc_total is not None else 0,
        ]
        if disc_mp4[0] == 0 and disc_mp4[1] == 0:
            disc_mp4 = None

        track_mp4 = [
            self.track if self.track is not None else 0,
            self.track_total if self.track_total is not None else 0,
        ]
        if track_mp4[0] == 0 and track_mp4[1] == 0:
            track_mp4 = None

        if isinstance(self.date, datetime.date):
            if date_format is None:
                date_mp4 = self.date.isoformat()
            else:
                date_mp4 = self.date.strftime(date_format)
        elif isinstance(self.date, str):
            date_mp4 = self.date
        else:
            date_mp4 = None

        mp4_tags = {
            "\xa9alb": self.album,
            "aART": self.album_artist,
            "plID": self.album_id,
            "soal": self.album_sort,
            "\xa9ART": self.artist,
            "atID": self.artist_id,
            "soar": self.artist_sort,
            "\xa9cmt": self.comment,
            "cpil": bool(self.compilation) if self.compilation is not None else None,
            "\xa9wrt": self.composer,
            "cmID": self.composer_id,
            "soco": self.composer_sort,
            "cprt": self.copyright,
            "\xa9day": date_mp4,
            "disk": disc_mp4,
            "pgap": bool(self.gapless) if self.gapless is not None else None,
            "\xa9gen": self.genre,
            "\xa9lyr": self.lyrics,
            "geID": self.genre_id,
            "stik": int(self.media_type) if self.media_type is not None else None,
            "rtng": int(self.rating) if self.rating is not None else None,
            "sfID": self.storefront,
            "\xa9nam": self.title,
            "cnID": self.title_id,
            "sonm": self.title_sort,
            "trkn": track_mp4,
            "xid ": self.xid,
            "----:com.apple.iTunes:barcode": (self.upc.encode("utf-8") if self.upc else None),
            "----:com.apple.iTunes:isrc": (self.isrc.encode("utf-8") if self.isrc else None),
            "----:com.apple.iTunes:label": (self.record_label.encode("utf-8") if self.record_label else None),
            "----:com.apple.iTunes:releasedate": (self.release_date.encode("utf-8") if self.release_date else None),
            "----:com.apple.iTunes:artists": ([a.encode("utf-8") for a in self.artists] if self.artists else None),
            "----:com.apple.iTunes:albumartists": ([a.encode("utf-8") for a in self.album_artists] if self.album_artists else None),
        }

        return {
            k: (
                v
                if isinstance(v, bool) or (isinstance(v, list) and k not in ("disk", "trkn"))
                else [v]
            )
            for k, v in mp4_tags.items()
            if v is not None
        }


@dataclass
class PlaylistTags:
    artist: str = None
    playlist_id: int = None
    title: str = None
    track: int = None


@dataclass
class StreamInfo:
    stream_url: str = None
    widevine_pssh: str = None
    playready_pssh: str = None
    fairplay_key: str = None
    codec: str = None
    width: int = None
    height: int = None
    drm_free: bool = False
    use_cenc: bool = False
    use_single_content_key: bool = True


@dataclass
class StreamInfoAv:
    media_id: str = None
    video_track: StreamInfo = None
    audio_track: StreamInfo = None
    file_format: MediaFileFormat = None


@dataclass
class DecryptionKey:
    kid: str = None
    key: str = None


@dataclass
class DecryptionKeyAv:
    video_track: DecryptionKey = None
    audio_track: DecryptionKey = None


@dataclass
class Cover:
    template_url: str = None
    file_extension: str = None
    url: str = None


@dataclass
class AppleMusicMedia:
    media_id: str
    is_library: bool = False
    index: int = 0
    total: int = 0
    partial: bool = True
    media_metadata: dict | None = None
    error: BaseException | None = None
    playlist_metadata: dict | None = None
    playlist_tags: PlaylistTags | None = None
    extra_tags: dict | None = None
    cover: Cover | None = None
    lyrics: Lyrics | None = None
    tags: MediaTags | None = None
    stream_info: StreamInfoAv | None = None
    decryption_key: DecryptionKeyAv | None = None


@dataclass
class AppleMusicUrlInfo:
    storefront: str = None
    type: str = None
    slug: str = None
    id: str = None
    sub_id: str = None
    library_storefront: str = None
    library_type: str = None
    library_id: str = None
