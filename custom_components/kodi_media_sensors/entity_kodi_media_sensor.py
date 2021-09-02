import logging
import json
from typing import Any, Dict, List, Optional
from pykodi import Kodi
from urllib import parse
from homeassistant.helpers.entity import Entity
from homeassistant.const import STATE_OFF, STATE_ON, STATE_PROBLEM, STATE_UNKNOWN
from .types import DeviceStateAttrs, KodiConfig
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class KodiMediaSensorEntity(Entity):
    """This super class should never be instanciated. It's ba parent class of all the kodi media sensors"""

    _attrs = {}
    _data = []
    _meta = None

    def __init__(
        self,
        kodi: Kodi,
        config: KodiConfig,
        hide_watched: bool = False,
        use_auth_url: bool = False,
    ) -> None:
        super().__init__()
        self._kodi = kodi
        self.define_base_url(config, use_auth_url)

    def define_base_url(self, config, use_auth_url):
        protocol = "https" if config["ssl"] else "http"
        auth = ""
        if (
            use_auth_url
            and config["username"] is not None
            and config["password"] is not None
        ):
            auth = f"{config['username']}:{config['password']}@"
        self._base_web_url = (
            f"{protocol}://{auth}{config['host']}:{config['port']}/image/image%3A%2F%2F"
        )

    async def async_call_method(self, method, **kwargs):
        logging.warning("This method is not implemented for the entity")

    async def call_method_kodi(self, result_key, method, args) -> List:
        result = None
        data = None
        try:
            # Parameters are passed using a **kwargs because the number of JSON parmeters depends on each function
            result = await self._kodi.call_method(method, **args)
        except Exception:
            _LOGGER.exception("Error updating sensor, is kodi running?")
            self._state = STATE_OFF

        if result:
            data = self._handle_result(result, result_key)
        else:
            self._state = STATE_OFF

        return data

    async def call_method_kodi_no_result(self, method, args):
        try:
            # Parameters are passed using a **kwargs because the number of JSON parmeters depends on each function
            await self._kodi.call_method(method, **args)
        except Exception:
            _LOGGER.exception("Error updating sensor, is kodi running?")
            self._state = STATE_OFF

    def _handle_result(self, result, result_key) -> List:
        error = result.get("error")
        if error:
            _LOGGER.error(
                "Error while fetching %s: [%d] %s"
                % (self.result_key, error.get("code"), error.get("message"))
            )
            self._state = STATE_PROBLEM
            return

        new_data: List[Dict[str, Any]] = result.get(result_key, [])
        if not new_data:
            _LOGGER.info(
                "No %s found after requesting data from Kodi, assuming empty."
                % result_key
            )
            self._state = STATE_UNKNOWN
            return

        self._state = STATE_ON
        return new_data

    @property
    def state(self) -> Optional[str]:
        return self._state

    def get_web_url(self, path: str) -> str:
        """Get the web URL for the provided path.

        This is used for fanart/poster images that are not a http url.  For
        example the path is local to the kodi installation or a path to
        an NFS share.

        :param path: The local/nfs/samba/etc. path.
        :returns: The web url to access the image over http.
        """
        if path.lower().startswith("http"):
            return path
        # This looks strange, but the path needs to be quoted twice in order
        # to work.
        # added Gautier : character @ causes encoding problems for thumbnails revrieved from http://...music@smb... Therefore, it is escaped in the first quote
        quoted_path2 = parse.quote(parse.quote(path, safe="@"))
        encoded = self._base_web_url + quoted_path2
        return encoded

    @property
    def device_state_attributes(self) -> DeviceStateAttrs:
        self._attrs.clear
        self._attrs["meta"] = json.dumps(self._meta)
        self._attrs["data"] = json.dumps(self._data)
        return self._attrs

    def init_attrs(self):
        self._meta = [
            {
                "sensor_entity_id": self.entity_id,
                "service_domain": DOMAIN,
                # "kodi_entity_id": self._kodi_entity_id,
            }
        ]
