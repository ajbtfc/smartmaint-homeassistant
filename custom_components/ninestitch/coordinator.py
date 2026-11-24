"""The feed from Ninestitch, polled every 60 seconds: jobs for the to-do lists, the calendar, counts,
the linked entities, and notifications to deliver.

The feed has an ETag; when nothing changed Ninestitch answers 304 and the last answer is kept.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthFailed, NinestitchApi, NinestitchError, PlanRequired
from .const import DOMAIN, FEED_INTERVAL
from .notify import async_notify_person

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .listener import ReadingListener

_LOGGER = logging.getLogger(__name__)


def plan_issue_id(entry_id: str) -> str:
    return f"plan_required_{entry_id}"


class NinestitchCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the feed."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: NinestitchApi, listener: ReadingListener) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=FEED_INTERVAL, config_entry=entry)
        self.api = api
        self.listener = listener
        self.etag: str | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data, self.etag = await self.api.feed(self.etag)
        except AuthFailed as err:
            # Disconnected in Ninestitch: Home Assistant asks to pair again (reauth)
            raise ConfigEntryAuthFailed(err.message) from err
        except PlanRequired as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                plan_issue_id(self.config_entry.entry_id),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="plan_required",
                translation_placeholders={"household": self.config_entry.title},
            )
            raise UpdateFailed(err.message) from err
        except NinestitchError as err:
            raise UpdateFailed(err.message) from err

        ir.async_delete_issue(self.hass, DOMAIN, plan_issue_id(self.config_entry.entry_id))
        if data is None:  # 304: unchanged
            return self.data
        self.listener.set_links(data.get("links") or [])
        await self._async_deliver(data.get("notifications") or [])
        return data

    async def _async_deliver(self, notifications: list[dict[str, Any]]) -> None:
        """Each notification to its person's phone (or Home Assistant's notifications), then
        acknowledged so Ninestitch doesn't send it again. One that fails to show is retried with
        the next poll, since it isn't acknowledged."""
        shown: list[int] = []
        for note in notifications:
            try:
                await async_notify_person(
                    self.hass,
                    note.get("person"),
                    title=str(note.get("title") or "Ninestitch"),
                    message=str(note.get("message") or ""),
                    tag=f"ninestitch_{note.get('work_order_id') or note.get('id')}",
                )
            except Exception:  # noqa: BLE001 - a notify service can fail in many ways
                _LOGGER.exception("Couldn't show a Ninestitch notification")
                continue
            shown.extend(int(i) for i in note.get("ids") or [note.get("id")] if i is not None)
        if not shown:
            return
        try:
            await self.api.ack(shown)
        except NinestitchError as err:
            _LOGGER.warning("Couldn't acknowledge notifications (they may be shown again): %s", err.message)
