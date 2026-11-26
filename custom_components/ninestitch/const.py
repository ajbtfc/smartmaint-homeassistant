"""Constants for the Ninestitch integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ninestitch"

# Ninestitch's production project. The address is public (the web app uses it too); a different
# one (staging) can be entered in the advanced options when pairing.
DEFAULT_URL: Final = "https://louiosrbcxohskeibsfe.supabase.co"

CONF_TOKEN: Final = "token"
CONF_URL: Final = "url"
CONF_CODE: Final = "code"
CONF_NAME: Final = "name"
CONF_HOUSEHOLD_ID: Final = "household_id"
CONF_DEVICE_ID: Final = "device_id"

FEED_INTERVAL: Final = timedelta(seconds=60)
CATALOGUE_INTERVAL: Final = timedelta(hours=24)
QUEUE_TICK: Final = timedelta(seconds=30)
RUNTIME_TICK: Final = timedelta(minutes=15)

# What the catalogue offers for linking (#34): sensors, binary sensors and thermostats.
CATALOGUE_DOMAINS: Final = ("sensor", "binary_sensor", "climate")

# Sending: on change, at most once a minute per entity; cumulative meters (odometer, run hours) at
# most hourly, or sooner on a meaningful change.
MIN_SEND_INTERVAL: Final = 60.0
CUMULATIVE_SEND_INTERVAL: Final = 3600.0
CUMULATIVE_MEANINGFUL_CHANGE: Final = 0.01  # 1 % of the last value sent (at least 1 unit)

# The offline queue: persisted, capped at 24 hours or 10,000 readings.
QUEUE_MAX_READINGS: Final = 10_000
QUEUE_MAX_AGE: Final = 24 * 3600.0
MAX_BATCH: Final = 500

IGNORED_STATES: Final = ("unknown", "unavailable", "none", "")

# A climate entity counts as running while it heats or cools.
CLIMATE_RUNNING_ACTIONS: Final = ("heating", "cooling")

PLATFORMS: Final = ("todo", "calendar", "sensor")
