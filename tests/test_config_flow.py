"""Adding Ninestitch with a pairing code, the ways it can go wrong, and pairing again (#34)."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ninestitch.config_flow import normalise_code
from custom_components.ninestitch.const import DOMAIN

from .conftest import TOKEN, URL, error, feed

PAIRED = {"token": TOKEN, "household": "Maple Street", "device_id": "dev-1"}


@pytest.fixture(autouse=True)
def no_setup():
    with (
        patch("custom_components.ninestitch.async_setup_entry", return_value=True),
        patch("custom_components.ninestitch.async_unload_entry", return_value=True),
    ):
        yield


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_pairing_code_becomes_a_token_and_the_household_is_the_title(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{URL}/ha-pair", json=PAIRED)
    aioclient_mock.get(f"{URL}/ha-feed", json=feed())
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "abcd efgh", "name": "Home"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Maple Street"
    assert result["data"]["token"] == TOKEN
    assert result["data"]["household_id"] == "hh-1"
    assert result["result"].unique_id == "hh-1"
    sent = aioclient_mock.mock_calls[0][2]
    assert sent == {"code": "ABCD-EFGH", "name": "Home"}
    assert "Authorization" not in (aioclient_mock.mock_calls[0][3] or {}), "pairing needs no token"
    assert aioclient_mock.mock_calls[1][3]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, error("pairing_code_invalid"), "invalid_code"),
        (410, error("pairing_code_expired"), "expired_code"),
        (429, error("pairing_rate_limited"), "rate_limited"),
        (402, error("plan_requires_live_sensors"), "plan_required"),
        (503, error("not_configured"), "cannot_connect"),
        # production before the Home Assistant endpoints are released there (Supabase's own 404)
        (404, {"code": "NOT_FOUND", "message": "Requested function was not found"}, "server_not_ready"),
    ],
)
async def test_what_went_wrong_is_said_plainly(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int, body: dict, expected: str
) -> None:
    aioclient_mock.post(f"{URL}/ha-pair", status=status, json=body)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "ABCD-EFGH"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_no_internet(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{URL}/ha-pair", exc=aiohttp.ClientError("down"))
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "ABCD-EFGH"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_the_same_household_isnt_added_twice(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="hh-1", data={"token": "old"}).add_to_hass(hass)
    aioclient_mock.post(f"{URL}/ha-pair", json=PAIRED)
    aioclient_mock.get(f"{URL}/ha-feed", json=feed())
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "ABCD-EFGH"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_disconnected_in_ninestitch_pair_again_with_a_new_code(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="hh-1",
        title="Maple Street",
        data={"token": "sm_ha_old", "url": "https://louiosrbcxohskeibsfe.supabase.co", "household_id": "hh-1"},
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(f"{URL}/ha-pair", json={**PAIRED, "token": "sm_ha_new"})
    aioclient_mock.get(f"{URL}/ha-feed", json=feed())
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "WXYZ-2345"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"] == "sm_ha_new"


async def test_a_code_from_another_household_doesnt_replace_this_one(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="hh-1", data={"token": "sm_ha_old", "household_id": "hh-1"})
    entry.add_to_hass(hass)
    aioclient_mock.post(f"{URL}/ha-pair", json=PAIRED)
    aioclient_mock.get(f"{URL}/ha-feed", json=feed(household_id="hh-2"))
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "WXYZ-2345"})
    assert result["reason"] == "wrong_household"
    assert entry.data["token"] == "sm_ha_old"


def test_codes_are_typed_however() -> None:
    assert normalise_code(" abcd-efgh ") == "ABCD-EFGH"
    assert normalise_code("ABCDEFGH") == "ABCD-EFGH"
    assert normalise_code("abcd efgh") == "ABCD-EFGH"


async def test_a_test_server_can_be_used_in_advanced_mode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    staging = "https://lkcxpgtgtvoieqcuaygh.supabase.co"
    aioclient_mock.post(f"{staging}/functions/v1/ha-pair", json=PAIRED)
    aioclient_mock.get(f"{staging}/functions/v1/ha-feed", json=feed())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER, "show_advanced_options": True}
    )
    assert "url" in result["data_schema"].schema
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": "ABCD-EFGH", "url": staging + "/"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["url"] == staging
