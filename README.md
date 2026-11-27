# Ninestitch for Home Assistant

Connects Home Assistant to [Ninestitch](https://github.com/ajbtfc/SmartMaint), the household
maintenance app. Part of **Ninestitch Plus** (there's a free 14-day trial).

**In:** the sensors you choose keep Ninestitch up to date. An odometer moves mileage routines along,
a thermostat's heating and cooling hours time the filter change, and a leak or low-battery sensor
creates a job straight away.

**Out:** each person's open jobs as a to-do list (`todo.ninestitch_sam`; tick one off and it's done
in Ninestitch), a calendar of what's due, open and overdue counts for dashboards, and notifications
on your phone when a job is assigned to you or something urgent comes up.

## Install

1. In HACS: ⋮ → **Custom repositories** → add `https://github.com/ajbtfc/smartmaint-homeassistant`,
   category **Integration**. Then find **Ninestitch** in HACS, download it, and restart Home Assistant.
2. In Ninestitch: **Household → Home Assistant → Connect Home Assistant**. It shows a code like
   `ABCD-EFGH`, which works once, for 10 minutes.
3. In Home Assistant: **Settings → Devices & services → Add integration → Ninestitch**, and type the
   code. That's all: no passwords or addresses.
4. Back in Ninestitch, choose what to send. It suggests what each sensor should do, from its type,
   unit and area. Under **Who is who**, match each household member to their Home Assistant person,
   so to-do lists and notifications reach the right phone.

## What it creates

| Entity | What it is |
|---|---|
| `todo.ninestitch_<person>` | That person's open jobs. Ticking one off marks it done in Ninestitch, as them. If it needs readings first (the mileage at an oil change), it stays open and a notification links to it. |
| `todo.ninestitch_everyone` | Jobs not assigned to anyone matched under Who is who |
| `calendar.ninestitch_<household>_due` | Jobs' due dates, and when routines are next due |
| `sensor.ninestitch_<household>_open_jobs`, `…_overdue_jobs` | Counts for dashboards |
| `sensor.ninestitch_<household>_readings_waiting_to_send` | Readings queued while Ninestitch can't be reached (diagnostic) |

Notifications go to each person's phone through the Home Assistant app (`notify.mobile_app_*`, found
from their person's device trackers). Someone without the app, or not matched, gets them in Home
Assistant's notifications. Which kinds are sent is set in Ninestitch.

## How it behaves

- **Only linked entities send values.** Home Assistant uploads a list of its sensors, binary sensors,
  thermostats and people (names, areas, units, current states) on start and daily, for the setup
  screen. Readings go only for entities you linked in Ninestitch; changes there apply within a minute.
- **Not chatty.** Changes are sent at most once a minute per entity, and the last state is never
  dropped. Odometers and run hours are sent at most hourly, or sooner on a 1% change. `unknown` and
  `unavailable` aren't sent.
- **Run hours.** For a thermostat (or any on/off entity) linked as "Counts run hours", the
  integration counts the hours it heats or cools, keeps the count across restarts, and sends the
  total. A run-hours sensor you already have (an Integral helper) is sent as it is.
- **Offline.** Readings wait in a queue saved to disk, for up to 24 hours or 10,000 readings, and
  are retried with backoff. Each keeps the time it happened, so resending one Ninestitch already has
  counts as a duplicate, never a second reading.
- **Disconnected in Ninestitch:** Home Assistant asks for a new code (Settings → Repairs). Readings
  wait meanwhile.
- **Household not on Plus:** a repair issue says so; readings wait (24 hours) and everything resumes
  when it's back on Plus.
- **Diagnostics:** the integration's ⋮ → Download diagnostics shows what it's listening to, the queue
  and the last answers, without the token.

Using a test server: turn on **Advanced mode** in your Home Assistant profile, and the pairing step
asks for the Ninestitch server address.

## Development

```bash
python3.13 -m venv .venv && . .venv/bin/activate && pip install -r requirements_test.txt ruff
```

```bash
pytest -q && ruff check . && ruff format --check .
```

The tests run against a fake Ninestitch (`tests/conftest.py`), shaped like the real endpoints in
`supabase/functions/ha-*` of the Ninestitch repository.

## License

MIT, see [LICENSE](LICENSE). This covers the integration only.
