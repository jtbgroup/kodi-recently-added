import logging
import json
import time
from operator import itemgetter
from typing import Optional, Dict, List, Any
from homeassistant.helpers.entity import Entity
from urllib import parse
from .entity_kodi_media_sensor import KodiMediaSensorEntity
from homeassistant.const import STATE_OFF, STATE_ON
from pykodi import Kodi
from .const import (
    KEY_ALBUMS,
    KEY_SONGS,
    KEY_ARTISTS,
    KEY_MOVIES,
    KEY_ALBUM_DETAILS,
    KEY_TVSHOWS,
    KEY_CHANNELS,
    KEY_TVSHOW_SEASONS,
    KEY_TVSHOW_SEASON_DETAILS,
    KEY_TVSHOW_EPISODES,
    ENTITY_SENSOR_SEARCH,
    ENTITY_NAME_SENSOR_SEARCH,
    OPTION_SEARCH_LIMIT_DEFAULT_VALUE,
)
from .types import DeviceStateAttrs, KodiConfig


_LOGGER = logging.getLogger(__name__)


class KodiSearchEntity(KodiMediaSensorEntity):
    """This sensor is dedicated to the search functionality of Kodi"""

    _search_limit = OPTION_SEARCH_LIMIT_DEFAULT_VALUE
    _search_moment = 0
    _clear_timer = 300

    def __init__(
        self, kodi: Kodi, config: KodiConfig, kodi_entity_id: str, search_limit: int,
    ):
        super().__init__(kodi, config)
        self._state = STATE_ON
        self._kodi_entity_id = kodi_entity_id
        self._search_limit = search_limit

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return ENTITY_NAME_SENSOR_SEARCH

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return ENTITY_SENSOR_SEARCH

    async def async_update(self):
        _LOGGER.debug("> Update Search sensor")
        if (
            self._search_moment > 0
            and (time.perf_counter() - self._search_moment) > self._clear_timer
        ):
            await self.clear(False)

        self.init_attrs()

    async def async_call_method(self, method, **kwargs):
        self._search_moment = time.perf_counter()
        args = ", ".join(f"{key}={value}" for key, value in kwargs.items())
        _LOGGER.debug("calling method " + method + " with arguments " + args)
        self._meta[0]["method"] = method
        self._meta[0]["args"] = args

        if method == "search":
            item = kwargs.get("item")
            media_type = item.get("media_type")
            if media_type == "all":
                await self.search(item.get("value"))
            elif media_type == "artist":
                await self.search_artist(item.get("value"))
            elif media_type == "tvshow":
                await self.search_tvshow(item.get("value"))
            else:
                raise ValueError("The given media type is unsupported: " + media_type)
            await self.async_update()
        elif method == "clear":
            await self.clear(True)
        elif method == "play":
            if kwargs.get("songid") is not None:
                await self.play_song(kwargs.get("songid"))
            if kwargs.get("albumid") is not None:
                await self.play_album(kwargs.get("albumid"))
            if kwargs.get("movieid") is not None:
                await self.play_movie(kwargs.get("movieid"))
            if kwargs.get("episodeid") is not None:
                await self.play_episode(kwargs.get("episodeid"))

        else:
            raise ValueError("The given method is unsupported: " + method)

    async def clear(self, force_update):
        self._search_moment = 0
        del self._data[:]
        if force_update:
            await self.async_update()
        _LOGGER.debug("Clearded !")

    async def play_item(self, playlistid, item_name, item_value):
        _LOGGER.debug(item_value)
        if not isinstance(item_value, (list, tuple)):
            insertable = [item_value]
            item_value = insertable

        idx = 1
        for item in item_value:
            await self.call_method_kodi_no_result(
                "Playlist.Insert",
                {"playlistid": playlistid, "position": idx, "item": {item_name: item},},
            )
            idx = idx + 1

        await self.call_method_kodi_no_result(
            "Player.Open", {"item": {"playlistid": playlistid, "position": 1}},
        )

    async def play_song(self, songid):
        await self.play_item(0, "songid", songid)

    async def play_album(self, albumid):
        await self.play_item(0, "albumid", albumid)

    async def play_movie(self, movieid):
        await self.play_item(1, "movieid", movieid)

    async def play_episode(self, episodeid):
        await self.play_item(1, "episodeid", episodeid)

    async def search_tvshow(self, value):
        card_json = []
        self._data.clear

        if value is None or value == "":
            _LOGGER.warning("The argument 'value' passed is empty")
            return
        try:
            tvshow_season_resultset = await self.kodi_search_tvshow_seasons(value)
            tvshow_season_data: List[Dict[str, Any]] = list()

            if tvshow_season_resultset is not None and len(tvshow_season_resultset) > 0:
                for tvshow_season in tvshow_season_resultset:
                    season_number = tvshow_season["season"]

                    tvshow_episodes_resultset = await self.kodi_search_tvshow_episodes(
                        value, season_number
                    )

                    season = {
                        "title": tvshow_season["label"],
                        "seasonid": tvshow_season["seasonid"],
                        "season": tvshow_season["season"],
                        "thumbnail": tvshow_season["thumbnail"],
                        "episodes": tvshow_episodes_resultset,
                    }

                    tvshow_season_data.append(season)
            self.add_result(
                self.format_tvshow_season_details(tvshow_season_data), card_json
            )

            self._state = STATE_ON
        except Exception:
            _LOGGER.exception("Error updating sensor, is kodi running?")
            self._state = STATE_OFF

        self._data = card_json

    async def search_artist(self, value):
        if value is None or value == "":
            _LOGGER.warning("The argument 'value' passed is empty")
            return
        try:
            songs_resultset = await self.kodi_search_songs(value, True, "artistid")

        except Exception:
            _LOGGER.exception("Error updating sensor, is kodi running?")
            self._state = STATE_OFF

        if songs_resultset is not None and len(songs_resultset) > 0:
            album_id_set = set()
            albums_data: List[Dict[str, Any]] = list()
            songs_data = list(
                filter(
                    lambda d: d["albumid"] is None or d["albumid"] == "",
                    songs_resultset,
                )
            )

            for song in songs_resultset:
                if song["albumid"] is not None and song["albumid"] != "":
                    album_id_set.add(song["albumid"])

            for album_id in album_id_set:
                album_resultset = await self.kodi_search_albumdetails(album_id)

                album_songs = list(
                    filter(lambda d: d["albumid"] == album_id, songs_resultset)
                )

                if album_resultset["label"] is None:
                    _LOGGER.exception("?????????????" + album_id)

                album = {
                    "albumid": album_id,
                    "title": album_resultset["label"],
                    "year": album_resultset["year"],
                    "thumbnail": album_resultset["thumbnail"],
                    "songs": album_songs,
                }
                albums_data.append(album)

            card_json = []
            self.add_result(self.format_songs(songs_data), card_json)
            self.add_result(self.format_album_details(albums_data), card_json)

            self._data.clear
            self._data = card_json
            self._state = STATE_ON

    async def kodi_search_albumdetails(self, value):
        return await self.call_method_kodi(
            KEY_ALBUM_DETAILS,
            "AudioLibrary.GetAlbumDetails",
            {
                "properties": [
                    "albumlabel",
                    "artist",
                    "year",
                    "artistid",
                    "thumbnail",
                    "style",
                    "genre",
                ],
                "albumid": value,
            },
        )

    async def kodi_search_songs(
        self, value, unlimited: bool = False, filter_field: str = "title"
    ):
        _limits = {"start": 0}
        if not unlimited:
            _limits["end"] = self._search_limit

        _filter = {}
        if filter_field == "title":
            _filter["field"] = "title"
            _filter["operator"] = "contains"
            _filter["value"] = value
        elif filter_field == "artistid":
            _filter["artistid"] = value

        return await self.call_method_kodi(
            KEY_SONGS,
            "AudioLibrary.GetSongs",
            {
                "properties": [
                    "title",
                    "album",
                    "albumid",
                    "artist",
                    "artistid",
                    "track",
                    "year",
                    "duration",
                    "thumbnail",
                ],
                "limits": _limits,
                "sort": {
                    "method": "track",
                    "order": "ascending",
                    "ignorearticle": True,
                },
                "filter": _filter,
            },
        )

    async def kodi_search_tvshow_episodes(self, tvshowid, season):
        _limits = {"start": 0}

        return await self.call_method_kodi(
            KEY_TVSHOW_EPISODES,
            "VideoLibrary.GetEpisodes",
            {
                "properties": ["title", "rating", "episode", "season",],
                "limits": _limits,
                "sort": {
                    "method": "episode",
                    "order": "ascending",
                    "ignorearticle": True,
                },
                "tvshowid": tvshowid,
                "season": season,
            },
        )

    async def kodi_search_tvshow_seasons(self, value):
        _limits = {"start": 0}

        return await self.call_method_kodi(
            KEY_TVSHOW_SEASONS,
            "VideoLibrary.GetSeasons",
            {
                "properties": ["season", "showtitle", "thumbnail",],
                "limits": _limits,
                "sort": {
                    "method": "season",
                    "order": "ascending",
                    "ignorearticle": True,
                },
                "tvshowid": value,
            },
        )

    async def kodi_search_albums(self, value, unlimited: bool = False):
        limits = {"start": 0}
        if not unlimited:
            limits["end"] = self._search_limit
        return await self.call_method_kodi(
            KEY_ALBUMS,
            "AudioLibrary.GetAlbums",
            {
                "properties": ["title", "artist", "year", "thumbnail", "artistid"],
                "limits": limits,
                "sort": {
                    "method": "title",
                    "order": "ascending",
                    "ignorearticle": True,
                },
                "filter": {"field": "album", "operator": "contains", "value": value,},
            },
        )

    async def kodi_search_artists(self, value, unlimited: bool = False):
        limits = {"start": 0}
        if not unlimited:
            limits["end"] = self._search_limit
        return await self.call_method_kodi(
            KEY_ARTISTS,
            "AudioLibrary.GetArtists",
            {
                "properties": ["thumbnail", "mood", "genre", "style"],
                "limits": limits,
                "sort": {
                    "method": "title",
                    "order": "ascending",
                    "ignorearticle": True,
                },
                "filter": {"field": "artist", "operator": "contains", "value": value,},
            },
        )

    async def kodi_search_movies(self, value, unlimited: bool = False):
        limits = {"start": 0}
        if not unlimited:
            limits["end"] = self._search_limit
        return await self.call_method_kodi(
            KEY_MOVIES,
            "VideoLibrary.GetMovies",
            {
                "properties": ["thumbnail", "title", "year", "art", "genre"],
                "limits": limits,
                "sort": {
                    "method": "title",
                    "order": "ascending",
                    "ignorearticle": True,
                },
                "filter": {"field": "title", "operator": "contains", "value": value,},
            },
        )

    async def kodi_search_channels(self, value):
        limits = {"start": 0}
        allChannels = await self.call_method_kodi(
            KEY_CHANNELS,
            "PVR.GetChannels",
            {"properties": ["uniqueid",], "limits": limits, "channelgroupid": "alltv",},
        )
        filteredChannels = []
        for channel in allChannels:
            if value in channel["label"]:
                filteredChannels.append(channel)
        return filteredChannels

    async def kodi_search_tvshows(self, value, unlimited: bool = False):
        limits = {"start": 0}
        if not unlimited:
            limits["end"] = self._search_limit
        return await self.call_method_kodi(
            KEY_TVSHOWS,
            "VideoLibrary.GetTVShows",
            {
                "properties": [
                    "title",
                    "thumbnail",
                    "playcount",
                    "dateadded",
                    "episode",
                    "rating",
                    "year",
                    "season",
                    "genre",
                ],
                "limits": limits,
                "sort": {"method": "title", "order": "ascending",},
                "filter": {"field": "title", "operator": "contains", "value": value,},
            },
        )

    async def search(self, value):
        if value is None or value == "":
            _LOGGER.warning("The argument 'value' passed is empty")
            return

        _LOGGER.debug("Searching for '" + value + "'")

        try:
            songs = await self.kodi_search_songs(value)
            albums = await self.kodi_search_albums(value)
            artists = await self.kodi_search_artists(value)
            movies = await self.kodi_search_movies(value)
            tvshows = await self.kodi_search_tvshows(value)
            channels = await self.kodi_search_channels(value)
        except Exception:
            _LOGGER.exception("Error updating sensor, is kodi running?")
            self._state = STATE_OFF

        card_json = []
        self.add_result(self.format_songs(songs), card_json)
        self.add_result(self.format_albums(albums), card_json)
        self.add_result(self.format_artists(artists), card_json)
        self.add_result(self.format_movies(movies), card_json)
        self.add_result(self.format_tvshows(tvshows), card_json)
        self.add_result(self.format_channels(channels), card_json)

        self._data.clear
        self._data = card_json
        self._state = STATE_ON

    def add_result(self, data, target):
        if data is not None and len(data) > 0:
            for row in data:
                target.append(row)

    def format_album_details(self, values):
        if values is None:
            return None

        # values.sort(key=lambda tup: tup["year"], reverse=True)

        values.sort(key=itemgetter("year"), reverse=True)

        result = []
        for item in values:
            albumid = item["albumid"]
            card = {
                "object_type": "albumdetail",
                "title": item["title"],
                "year": item["year"],
                "albumid": albumid,
                "songs_count": len(item["songs"]),
                "songs": self.format_songs(item["songs"]),
            }
            thumbnail = item["thumbnail"]
            if thumbnail:
                thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                card["thumbnail"] = thumbnail

            result.append(card)
        return result

    def format_tvshow_season_details(self, values):
        if values is None:
            return None
        result = []
        for item in values:
            card = {
                "object_type": "seasondetail",
                "title": item["title"],
                "season": item["season"],
                "seasonid": item["seasonid"],
                "episodes": self.format_tvshow_episode_details(item["episodes"]),
            }
            thumbnail = item["thumbnail"]
            if thumbnail:
                thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                card["thumbnail"] = thumbnail

            result.append(card)
        return result

    def format_tvshow_episode_details(self, values):
        if values is None:
            return None

        result = []
        for item in values:
            card = {
                "object_type": "episodedetail",
                "title": item["title"],
                "season": item["season"],
                "episode": item["episode"],
                "episodeid": item["episodeid"],
                "label": item["label"],
            }
            result.append(card)
        return result

    def format_albums(self, values):
        if values is None:
            return None

        result = []
        for item in values:
            card = {
                "object_type": "album",
                "artist": ",".join(item["artist"]),
                "albumid": item["albumid"],
                "artistid": item["artistid"][0],
            }

            self.add_attribute("title", item, "title", card)
            self.add_attribute("year", item, "year", card)
            self.add_attribute("albumid", item, "albumid", card)

            thumbnail = item["thumbnail"]
            if thumbnail:
                thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                card["thumbnail"] = thumbnail

            result.append(card)
        return result

    def format_artists(self, values):
        if values is None:
            return None

        result = []
        for item in values:
            card = {"object_type": "artist"}
            self.add_attribute("artist", item, "artist", card)
            self.add_attribute("artistid", item, "artistid", card)
            result.append(card)
        return result

    def format_movies(self, values):
        if values is None:
            return None

        result = []
        for item in values:
            card = {
                "object_type": "movie",
                "genre": ", ".join(item["genre"]),
            }

            self.add_attribute("movieid", item, "movieid", card)
            self.add_attribute("title", item, "title", card)
            self.add_attribute("year", item, "year", card)

            thumbnail = item["thumbnail"]
            if thumbnail:
                thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                card["thumbnail"] = thumbnail

            try:
                fanart = item["art"].get("fanart", "")
                poster = item["art"].get("poster", "")
                if fanart:
                    fanart = self.get_web_url(parse.unquote(fanart)[8:].strip("/"))
                if poster:
                    poster = self.get_web_url(parse.unquote(poster)[8:].strip("/"))
                card["fanart"] = fanart
                card["poster"] = poster
            except KeyError:
                _LOGGER.warning("Error parsing key from movie blob: %s", item)
                continue

            result.append(card)
        return result

    def format_channels(self, values):
        if values is None:
            return None

        result = []
        for item in values:
            card = {
                "object_type": "channel",
                "channelid": item["channelid"],
            }

            self.add_attribute("channelid", item, "channelid", card)
            self.add_attribute("label", item, "label", card)

            result.append(card)
        return result

    def format_tvshows(self, values):
        if values is None:
            return None

        result = []
        for item in values:
            card = {
                "object_type": "tvshow",
                "genre": ", ".join(item["genre"]),
                "number": "S{:0>2}E{:0>2}".format(item["season"], item["episode"]),
            }

            self.add_attribute("tvshowid", item, "tvshowid", card)
            self.add_attribute("title", item, "title", card)
            self.add_attribute("year", item, "year", card)

            thumbnail = item["thumbnail"]
            if thumbnail:
                thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                card["thumbnail"] = thumbnail

            rating = round(item["rating"], 1)
            if rating:
                rating = f"\N{BLACK STAR} {rating}"
            card["rating"] = rating

            result.append(card)
        return result

    def format_songs(self, values):
        if values is None:
            return None

        result = []
        for item in values:
            card = {
                "object_type": "song",
                "artist": ", ".join(item["artist"]),
                "artistid": item["artistid"][0],
            }
            self.add_attribute("title", item, "title", card)
            self.add_attribute("album", item, "album", card)
            self.add_attribute("year", item, "year", card)
            self.add_attribute("songid", item, "songid", card)
            self.add_attribute("track", item, "track", card)
            self.add_attribute("duration", item, "duration", card)

            thumbnail = item["thumbnail"]
            if thumbnail:
                thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                card["thumbnail"] = thumbnail

            result.append(card)
        return result

    def add_attribute(self, attribute_name, data, target_attribute_name, target):
        if attribute_name in data:
            target[target_attribute_name] = data[attribute_name]
