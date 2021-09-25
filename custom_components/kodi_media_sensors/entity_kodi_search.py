import logging
import homeassistant
import json
import time
from operator import itemgetter
from homeassistant import core
from typing import Optional, Dict, List, Any
from homeassistant.helpers.entity import Entity
from urllib import parse
from .entity_kodi_media_sensor import KodiMediaSensorEntity
from homeassistant.const import STATE_OFF, STATE_ON, EVENT_STATE_CHANGED
from pykodi import Kodi
from .const import (
    KEY_ALBUMS,
    KEY_SONGS,
    KEY_ARTISTS,
    KEY_MOVIES,
    KEY_ALBUM_DETAILS,
    KEY_TVSHOWS,
    KEY_TVSHOW_SEASONS,
    KEY_TVSHOW_SEASON_DETAILS,
    KEY_TVSHOW_EPISODES,
    ENTITY_SENSOR_SEARCH,
    ENTITY_NAME_SENSOR_SEARCH,
    OPTION_SEARCH_LIMIT_DEFAULT_VALUE,
)
from .types import DeviceStateAttrs, KodiConfig

_LOGGER = logging.getLogger(__name__)
ACTION_DO_NOTHING = "nothing"
ACTION_CLEAR = "clear"
ACTION_REFRESH_META = "refresh_meta"

METHOD_SEARCH = "search"
METHOD_CLEAR = "clear"
METHOD_PLAY = "play"
SEARCH_MEDIA_TYPE_ALL = "all"
SEARCH_MEDIA_TYPE_RECENT = "recent"
SEARCH_MEDIA_TYPE_ARTIST = "artist"
SEARCH_MEDIA_TYPE_TVSHOW = "tvshow"
PLAY_ATTR_SONGID = "songid"
PLAY_ATTR_ALBUMID = "albumid"
PLAY_ATTR_MOVIEID = "movieid"
PLAY_ATTR_EPISODEID = "episodeid"

PROPS_TVSHOW = [
    "title",
    "thumbnail",
    "playcount",
    "dateadded",
    "episode",
    "rating",
    "year",
    "season",
    "genre",
]

PROPS_SONG = [
    "title",
    "album",
    "albumid",
    "artist",
    "artistid",
    "track",
    "year",
    "duration",
    "genre",
    "thumbnail",
]

PROPS_MOVIE = ["thumbnail", "title", "year", "art", "genre"]
PROPS_ALBUM = ["thumbnail", "title", "year", "art", "genre", "artist", "artistid"]
PROPS_ARTIST = ["thumbnail", "mood", "genre", "style"]
PROPS_ALBUM_DETAIL = [
    "albumlabel",
    "artist",
    "year",
    "artistid",
    "thumbnail",
    "style",
    "genre",
]


class KodiSearchEntity(KodiMediaSensorEntity):
    """This sensor is dedicated to the search functionality of Kodi"""

    _search_limit = OPTION_SEARCH_LIMIT_DEFAULT_VALUE
    _search_moment = 0
    _clear_timer = 300

    def __init__(
        self,
        hass,
        kodi: Kodi,
        kodi_entity_id,
        config: KodiConfig,
        search_limit: int,
    ):
        super().__init__(kodi, config)
        self._hass = hass
        self._search_limit = search_limit
        homeassistant.helpers.event.async_track_state_change_event(
            hass, kodi_entity_id, self.__handle_event
        )

        kodi_state = self._hass.states.get(kodi_entity_id)
        if kodi_state is None or kodi_state == STATE_OFF:
            self._state = STATE_OFF
        else:
            self._state = STATE_ON

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return ENTITY_NAME_SENSOR_SEARCH

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return ENTITY_SENSOR_SEARCH

    async def __handle_event(self, event):
        new_kodi_event_state = str(event.data.get("new_state").state)

        action = ACTION_DO_NOTHING
        new_entity_state = STATE_ON

        if new_kodi_event_state == STATE_OFF and self._state != STATE_OFF:
            action = ACTION_CLEAR
            new_entity_state = STATE_OFF
        elif new_kodi_event_state != STATE_OFF and self._state != STATE_ON:
            action = ACTION_REFRESH_META

        self._state = new_entity_state

        id = event.context.id + " [" + new_kodi_event_state + "]"
        if action == ACTION_CLEAR:
            self._clear_all_data(id)
        if action == ACTION_REFRESH_META:
            self.purge_data(id)
            self.init_meta(id)

        if action != ACTION_DO_NOTHING:
            self.schedule_update_ha_state()

    async def async_update(self):
        """Update is only used to purge the search result"""
        _LOGGER.debug("> Update Search sensor")

        if self._state != STATE_OFF and len(self._meta) == 0:
            self.init_meta("Kodi Search update event")

        if (
            self._search_moment > 0
            and (time.perf_counter() - self._search_moment) > self._clear_timer
        ):
            await self._clear_result()

    async def async_call_method(self, method, **kwargs):
        self._search_moment = time.perf_counter()
        args = ", ".join(f"{key}={value}" for key, value in kwargs.items())
        _LOGGER.debug("calling method " + method + " with arguments " + args)
        self._meta[0]["method"] = method
        self._meta[0]["args"] = args

        if method == METHOD_SEARCH:
            item = kwargs.get("item")
            media_type = item.get("media_type")
            search_value = item.get("value")
            if media_type == SEARCH_MEDIA_TYPE_ALL:
                await self.search(search_value)
            elif media_type == SEARCH_MEDIA_TYPE_RECENT:
                await self.search_recent()
            elif media_type == SEARCH_MEDIA_TYPE_ARTIST:
                await self.search_artist(search_value)
            elif media_type == SEARCH_MEDIA_TYPE_TVSHOW:
                await self.search_tvshow(search_value)
            else:
                raise ValueError("The given media type is unsupported: " + media_type)

            self.init_meta("search method called")
            if media_type == SEARCH_MEDIA_TYPE_RECENT or search_value is not None:
                self.add_meta("search", "true")
            self.schedule_update_ha_state()

        elif method == METHOD_CLEAR:
            await self._clear_result()
            self.schedule_update_ha_state()
        elif method == METHOD_PLAY:
            if kwargs.get("songid") is not None:
                await self.play_song(kwargs.get(PLAY_ATTR_SONGID))
            if kwargs.get("albumid") is not None:
                await self.play_album(kwargs.get(PLAY_ATTR_ALBUMID))
            if kwargs.get("movieid") is not None:
                await self.play_movie(kwargs.get(PLAY_ATTR_MOVIEID))
            if kwargs.get("episodeid") is not None:
                await self.play_episode(kwargs.get(PLAY_ATTR_EPISODEID))

        else:
            raise ValueError("The given method is unsupported: " + method)

    async def _clear_result(self):
        self._search_moment = 0
        self.init_meta("clear results event")
        self.purge_data("clear results event")
        _LOGGER.debug("Kodi search result clearded")

    def _clear_all_data(self, event_id):
        self.purge_meta(event_id)
        self.purge_data(event_id)
        _LOGGER.debug("Kodi search result clearded")

    async def play_item(self, playlistid, item_name, item_value):
        _LOGGER.debug(item_value)
        if not isinstance(item_value, (list, tuple)):
            insertable = [item_value]
            item_value = insertable

        idx = 1
        for item in item_value:
            await self.call_method_kodi_no_result(
                "Playlist.Insert",
                {
                    "playlistid": playlistid,
                    "position": idx,
                    "item": {item_name: item},
                },
            )
            idx = idx + 1

        await self.call_method_kodi_no_result(
            "Player.Open",
            {"item": {"playlistid": playlistid, "position": 1}},
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
            self._add_result(
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
            # self._state = STATE_OFF

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
            self._add_result(self.format_songs(songs_data), card_json)
            self._add_result(self.format_album_details(albums_data), card_json)

            self._data.clear
            self._data = card_json
            # self._state = STATE_ON

    async def kodi_search_albumdetails(self, value):
        return await self.call_method_kodi(
            KEY_ALBUM_DETAILS,
            "AudioLibrary.GetAlbumDetails",
            {
                "properties": PROPS_ALBUM_DETAIL,
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
                "properties": PROPS_SONG,
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
                "properties": [
                    "title",
                    "rating",
                    "episode",
                    "season",
                ],
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
                "properties": [
                    "season",
                    "showtitle",
                    "thumbnail",
                ],
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
                "properties": PROPS_ALBUM,
                "limits": limits,
                "sort": {
                    "method": "title",
                    "order": "ascending",
                    "ignorearticle": True,
                },
                "filter": {
                    "field": "album",
                    "operator": "contains",
                    "value": value,
                },
            },
        )

    async def kodi_search_recent_albums(self, unlimited: bool = False):
        limits = {"start": 0}
        if not unlimited:
            limits["end"] = self._search_limit
        return await self.call_method_kodi(
            KEY_ALBUMS,
            "AudioLibrary.GetRecentlyAddedAlbums",
            {
                "properties": PROPS_ALBUM,
                "limits": limits,
            },
        )

    async def kodi_search_recent_songs(self, unlimited: bool = False):
        limits = {"start": 0}
        if not unlimited:
            limits["end"] = self._search_limit
        return await self.call_method_kodi(
            KEY_SONGS,
            "AudioLibrary.GetRecentlyAddedSongs",
            {
                "properties": PROPS_SONG,
                "limits": limits,
            },
        )

    async def kodi_search_recent_movies(self, unlimited: bool = False):
        limits = {"start": 0}
        if not unlimited:
            limits["end"] = self._search_limit
        return await self.call_method_kodi(
            KEY_MOVIES,
            "VideoLibrary.GetRecentlyAddedMovies",
            {
                "properties": PROPS_MOVIE,
                "limits": limits,
            },
        )

    async def kodi_search_recent_tvshow_episodes(self, unlimited: bool = False):
        limits = {"start": 0}
        if not unlimited:
            limits["end"] = self._search_limit
        return await self.call_method_kodi(
            KEY_TVSHOW_EPISODES,
            "VideoLibrary.GetRecentlyAddedEpisodes",
            {
                "properties": [
                    "title",
                    "rating",
                    "episode",
                    "season",
                ],
                "limits": limits,
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
                "properties": PROPS_ARTIST,
                "limits": limits,
                "sort": {
                    "method": "title",
                    "order": "ascending",
                    "ignorearticle": True,
                },
                "filter": {
                    "field": "artist",
                    "operator": "contains",
                    "value": value,
                },
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
                "properties": PROPS_MOVIE,
                "limits": limits,
                "sort": {
                    "method": "title",
                    "order": "ascending",
                    "ignorearticle": True,
                },
                "filter": {
                    "field": "title",
                    "operator": "contains",
                    "value": value,
                },
            },
        )

    async def kodi_search_tvshows(self, value, unlimited: bool = False):
        limits = {"start": 0}
        if not unlimited:
            limits["end"] = self._search_limit
        return await self.call_method_kodi(
            KEY_TVSHOWS,
            "VideoLibrary.GetTVShows",
            {
                "properties": PROPS_TVSHOW,
                "limits": limits,
                "sort": {
                    "method": "title",
                    "order": "ascending",
                },
                "filter": {
                    "field": "title",
                    "operator": "contains",
                    "value": value,
                },
            },
        )

    async def search_recent(self):
        _LOGGER.debug("Searching recents")
        try:
            songs = await self.kodi_search_recent_songs()
            albums = await self.kodi_search_recent_albums()
            movies = await self.kodi_search_recent_movies()
            episodes = await self.kodi_search_recent_tvshow_episodes()
        except Exception:
            _LOGGER.exception("Error updating sensor, is kodi running?")

        card_json = []
        self._add_result(self.format_songs(songs), card_json)
        self._add_result(self.format_albums(albums), card_json)
        self._add_result(self.format_movies(movies), card_json)
        self._add_result(self.format_tvshow_episode_details(episodes), card_json)

        self._data.clear
        self._data = card_json

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

        except Exception:
            _LOGGER.exception("Error updating sensor, is kodi running?")
            # self._state = STATE_OFF

        card_json = []
        self._add_result(self.format_songs(songs), card_json)
        self._add_result(self.format_albums(albums), card_json)
        self._add_result(self.format_artists(artists), card_json)
        self._add_result(self.format_movies(movies), card_json)
        self._add_result(self.format_tvshows(tvshows), card_json)

        self._data.clear
        self._data = card_json
        # self._state = STATE_ON

    def _add_result(self, data, target):
        if data is not None and len(data) > 0:
            for row in data:
                target.append(row)

    def format_album_details(self, values):
        if values is None:
            return None

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
                # thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                thumbnail = self._kodi.thumbnail_url(thumbnail)
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
                # thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                thumbnail = self._kodi.thumbnail_url(thumbnail)
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
                # thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                thumbnail = self._kodi.thumbnail_url(thumbnail)
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
                # thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                thumbnail = self._kodi.thumbnail_url(thumbnail)
                card["thumbnail"] = thumbnail

            try:
                fanart = item["art"].get("fanart", "")
                poster = item["art"].get("poster", "")
                if fanart:
                    # fanart = self.get_web_url(parse.unquote(fanart)[8:].strip("/"))
                    fanart = self._kodi.thumbnail_url(thumbnail)
                if poster:
                    # poster = self.get_web_url(parse.unquote(poster)[8:].strip("/"))
                    poster = self._kodi.thumbnail_url(thumbnail)
                card["fanart"] = fanart
                card["poster"] = poster
            except KeyError:
                _LOGGER.warning("Error parsing key from movie blob: %s", item)
                continue

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
                # thumbnail = self.get_web_url(parse.unquote(thumbnail)[8:].strip("/"))
                thumbnail = self._kodi.thumbnail_url(thumbnail)
                card["thumbnail"] = thumbnail

            rating = round(item["rating"], 1)
            if rating:
                rating = f"\N{BLACK STAR} {rating}"
            card["rating"] = rating

            result.append(card)
        return result
