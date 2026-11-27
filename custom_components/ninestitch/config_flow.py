"""Adding Ninestitch: type the pairing code Ninestitch shows; nothing else to copy.

Ninestitch → Household → Home Assistant → Connect shows a one-time code (10 minutes). Home Assistant
swaps it for a connection token, which Ninestitch keeps only as a hash. If the connection is later
disconnected in Ninestitch, Home Assistant asks for a new code (reauth).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CannotConnect, NinestitchApi, NinestitchError, PlanRequired
from .const import (
    CONF_CODE,
    CONF_DEVICE_ID,
    CONF_HOUSEHOLD_ID,
    CONF_NAME,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

ERRORS = {
    "pairing_code_invalid": "invalid_code",
    "pairing_code_expired": "expired_code",
    "pairing_rate_limited": "rate_limited",
}


def normalise_code(code: str) -> str:
    """'abcd efgh', 'ABCD-EFGH' → 'ABCD-EFGH' (as Ninestitch shows it)."""
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else code.strip().upper()


class NinestitchConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _async_pair(self, user_input: dict[str, Any], url: str) -> tuple[dict[str, Any] | None, str | None]:
        """(entry data, None) or (None, error key)."""
        api = NinestitchApi(async_get_clientsession(self.hass), url)
        try:
            paired = await api.pair(
                normalise_code(user_input[CONF_CODE]),
                user_input.get(CONF_NAME) or self.hass.config.location_name or "Home Assistant",
            )
            api.token = paired["token"]
            feed, _ = await api.feed()
        except PlanRequired:
            return None, "plan_required"
        except CannotConnect:
            return None, "cannot_connect"
        except NinestitchError as err:
            if err.status == 404:
                # this Ninestitch server doesn't have Home Assistant (yet): not released there, or
                # the wrong address
                return None, "server_not_ready"
            return None, ERRORS.get(err.code, "unknown")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error pairing with Ninestitch")
            return None, "unknown"
        feed = feed or {}
        return {
            CONF_URL: url,
            CONF_TOKEN: paired["token"],
            CONF_HOUSEHOLD_ID: feed.get("household_id"),
            CONF_DEVICE_ID: paired.get("device_id") or feed.get("device_id"),
            "household": paired.get("household") or feed.get("household") or "Ninestitch",
        }, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = (user_input.get(CONF_URL) or DEFAULT_URL).rstrip("/")
            data, error = await self._async_pair(user_input, url)
            if error:
                errors["base"] = error
            else:
                assert data is not None
                await self.async_set_unique_id(data[CONF_HOUSEHOLD_ID] or data[CONF_DEVICE_ID])
                self._abort_if_unique_id_configured()
                title = data.pop("household")
                return self.async_create_entry(title=title, data=data)

        schema: dict[Any, Any] = {
            vol.Required(CONF_CODE): str,
            vol.Optional(CONF_NAME, default=self.hass.config.location_name or "Home Assistant"): str,
        }
        if self.show_advanced_options:
            schema[vol.Optional(CONF_URL, default=DEFAULT_URL)] = str
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Disconnected in Ninestitch: a new pairing code, for the same household."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, error = await self._async_pair(user_input, entry.data.get(CONF_URL, DEFAULT_URL))
            if error:
                errors["base"] = error
            else:
                assert data is not None
                data.pop("household")
                if entry.unique_id and data[CONF_HOUSEHOLD_ID] and data[CONF_HOUSEHOLD_ID] != entry.unique_id:
                    return self.async_abort(reason="wrong_household")
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
            description_placeholders={"household": entry.title},
        )
