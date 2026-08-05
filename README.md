# EnergyKey for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?logo=homeassistantcommunitystore)](https://github.com/hacs/integration)
[![Validate](https://github.com/DevOpsDisaster/homeassistant-energykey/actions/workflows/validate.yml/badge.svg)](https://github.com/DevOpsDisaster/homeassistant-energykey/actions/workflows/validate.yml)
[![Latest release](https://img.shields.io/github/v/release/DevOpsDisaster/homeassistant-energykey?include_prereleases)](https://github.com/DevOpsDisaster/homeassistant-energykey/releases)
[![License](https://img.shields.io/github/license/DevOpsDisaster/homeassistant-energykey)](LICENSE)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DevOpsDisaster&repository=homeassistant-energykey&category=integration)

EnergyKey is an unofficial, read-only Home Assistant custom integration for
water and district-heating data exposed by EnergyKey supplier portals. It is
intended for Danish utilities that use a portal below `*.wt.energykey.dk`, with
`https://dinforsyning.wt.energykey.dk` as the default.

The integration uses private EnergyKey endpoints and is not developed,
supported, or endorsed by EnergyKey or your utility. Portal changes may break
it without notice.

Documentation:

- [Entities, history, and external statistics](docs/entities-and-data.md)
- [Installation](docs/installation.md) and [cookie authentication](docs/cookie-authentication.md)
- [Dashboard and ApexCharts guide](docs/dashboard.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Data, security, and privacy](docs/security-and-privacy.md)
- [Removal](docs/removal.md)
- [Contributing](CONTRIBUTING.md) and [release process](RELEASING.md)

The current sanitized schema and live acceptance are based on the Din
Forsyning tenant. Other hosts below `*.wt.energykey.dk` are accepted but should
be considered experimental until their data shape has been verified.

## What it provides

EnergyKey discovers every compatible meter in the account and creates one
Home Assistant device per active meter. Inactive historical meter records are
ignored. Device names include the supply type, the portal's custom meter name
when present, and the meter number. The meter number is also registered as the
Home Assistant device serial number. Every supported water or heat meter exposes:

- **Latest completed daily consumption** — the newest daily period marked as
  complete by EnergyKey.
- **Expected daily consumption** — EnergyKey's budget/expected value for the
  latest completed daily period, when the portal provides the stable
  `usageBudget` series.
- **Last successful heartbeat** — the latest successful session keepalive
  timestamp. This config-entry diagnostic entity is disabled by default.

Compatible district-heating meters additionally expose:

- **Latest completed daily heat volume** in a recognized volume unit.
- **Latest flow temperature** and **Latest return temperature**, when the
  portal exposes the verified internal temperature series and unit.

Each daily metric sensor also has a `data` attribute containing up to 13
calendar months of ordered portal history:

```yaml
data:
  - start: "2026-07-30T22:00:00+00:00"
    end: "2026-07-31T22:00:00+00:00"
    value: 0.31
    complete: true
```

Only explicitly recognized EnergyKey water and energy units are exposed.
Unknown units and ambiguous data series are skipped instead of guessed.
Cooling, prognosis, and billing entities are not included. Expected values are
shown as portal reference data; they are not treated as measured consumption.

## How it works

EnergyKey does not expose a supported authentication API for this use case.
You therefore sign in normally in a browser and copy the browser's `Cookie`
request header into Home Assistant. Home Assistant stores the bootstrap
cookies locally, maintains its own private HTTP session, and sends an EnergyKey
heartbeat when the session has been inactive for 15 minutes.

A definitive authentication rejection, including HTTP 401 or 403, latches the
client as rejected, stops the heartbeat loop, and starts one reauthentication
flow. Further polling attempts fail locally without contacting EnergyKey until
fresh cookies have been validated and the config entry has reloaded.

Setup waits only for meter and view discovery so Home Assistant can create the
meter devices promptly. The first current and historical data refresh starts
as config-entry-bound background work after setup returns; verified sensor
entities are added as their series and scaled units become available. The
first refresh asks for 13 calendar months in bounded monthly requests. Later
refreshes run every 24 hours and re-fetch the newest 45 days so delayed or
corrected supplier readings replace older values. Failed older chunks are
retried on later refreshes.

Complete actual water consumption, actual heat consumption, and heat volume
are imported into Home Assistant as external long-term statistics. The first
successful refresh backfills the available 13-month window. Later imports use
the same opaque statistic IDs and timestamps, so delayed or corrected portal
values revise existing rows rather than creating duplicates. Incomplete
periods and expected consumption are never imported as measured statistics.

The integration maintains a cumulative statistics offset as the local
13-month cache rolls forward. This preserves a continuous `sum` for existing
statistics while bounding private integration storage. Removing the config
entry also queues removal of its external statistics. The regular Recorder
history for the sensor entities themselves still begins at installation; only
the external long-term statistics are backfilled.

The large `data` attributes remain available to dashboards but are explicitly
excluded from Recorder to avoid Home Assistant's state-attribute storage
limit.

## Installation

Home Assistant 2026.7.0 or newer is required.

### HACS custom repository

1. Open HACS and select **Integrations**.
2. Open the menu, choose **Custom repositories**, enter
   `https://github.com/DevOpsDisaster/homeassistant-energykey`, and select the
   **Integration** category.
3. Download **EnergyKey** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and search for
   **EnergyKey**.

This repository targets custom-repository installation. It is not submitted to
the HACS default store in v1.

### Manual installation

Copy `custom_components/energykey` into your Home Assistant configuration so
the final path is `/config/custom_components/energykey`, then restart Home
Assistant and add the integration from **Settings → Devices & services**.

## Copying the Cookie header

Treat the copied value like a password. Paste it only into your own local Home
Assistant UI.

1. Open the EnergyKey portal in Chrome and sign in normally.
2. Open **Developer Tools → Network** and reload the page.
3. Select an authenticated request such as `heartbeat` or `itemGroups`.
4. Under **Headers → Request Headers**, find `Cookie` and copy its complete
   value. If Chrome shows the source view, you may copy the entire
   `Cookie: ...` line instead.
5. In the EnergyKey setup form, keep or change the portal URL and paste the
   copied value into **Cookie header**.

Equivalent Chrome, Edge, and Firefox instructions, together with reauth and
revocation guidance, are available in the
[cookie authentication guide](docs/cookie-authentication.md).

Do not paste a response `Set-Cookie` header. The integration requires both
`wt3SessionId` and `wt3login`, validates them against the portal, and rejects
redirects outside the configured origin.

## Reauthentication

EnergyKey sessions can expire even while they are kept active. When EnergyKey
definitively rejects the session, Home Assistant displays a repair and asks
for a fresh Cookie header. Sign in again, repeat the Chrome steps, and paste
cookies from the same EnergyKey account. Successful reauthentication updates
the existing config entry and preserves its device and entity identities.

Cookies from another account are rejected. To switch accounts, remove the
integration entry and create a new one.

## Dashboard example

The historical attribute can be rendered with the third-party ApexCharts Card.
Install the card and locate the correct entity IDs by following the
[dashboard guide](docs/dashboard.md) before using these examples.

For a ready-to-paste 31-day water and district-heating dashboard showing
actual consumption as columns, expected consumption as a dashed line, and
heat flow and return temperatures, see
[`examples/apexcharts-water-and-heat.yaml`](examples/apexcharts-water-and-heat.yaml).
Replace its six `REPLACE_*` entity IDs with the entities created for your
meters.

For cumulative actual-versus-expected area charts covering the retained
13-month window, see
[`examples/apexcharts-cumulative-water-and-heat.yaml`](examples/apexcharts-cumulative-water-and-heat.yaml).
The totals begin at zero at the first completed period visible in the chart;
they are consumption totals for that displayed window, not lifetime meter
readings.

```yaml
type: custom:apexcharts-card
graph_span: 30d
header:
  show: true
  title: Daily water consumption
series:
  - entity: sensor.water_meter_latest_completed_daily_consumption
    name: Water
    type: column
    data_generator: |
      return entity.attributes.data
        .filter((point) => point.complete)
        .map((point) => [new Date(point.start).getTime(), point.value]);
```

Replace the entity ID with the one created in your Home Assistant instance.
The newest incomplete period remains in `data` with `complete: false`, but is
not used as the sensor's latest completed state.

Expected consumption and heat volume use the same four-field history shape.
Temperature history uses the same four-field shape. EnergyKey does not provide
a reliable completion flag for temperature series directly, so the integration
derives it by matching each temperature period with the actual heat-consumption
period for that meter. No assumption is made about how recent the period is.
Actual consumption and
heat-volume external statistics can also be selected in Home Assistant's
statistics-based dashboards. Their IDs begin with `energykey:` and contain
only an opaque SHA-256-derived meter key.

## Data delay and session limitations

- Utility readings can arrive several days late. Daily refreshes revisit 45
  days of every discovered series to pick up late and corrected values and to
  revise the corresponding long-term statistics.
- The 15-minute heartbeat is designed around an observed 20–25-minute idle
  timeout. A brief network failure is retried after one minute.
- Home Assistant cannot send heartbeats while stopped. A long shutdown may
  require fresh cookies after restart.
- Heartbeats cannot override an absolute server-side expiry, a logout, a
  password or identity change, or the utility revoking the session.
- If a refresh temporarily fails, the last valid values remain in coordinator
  memory while the entities are marked unavailable.

## Security and privacy

The Cookie header grants access to your EnergyKey portal. Anyone who obtains it
may be able to read private utility data until the session expires or is
revoked.

- Never put cookies in YAML, screenshots, issues, logs, fixtures, chat, or Git.
- Use only the local Home Assistant setup form.
- Protect Home Assistant backups and `.storage`, where session state is kept.
- To revoke access, sign out of EnergyKey, invalidate active portal sessions if
  the portal offers that option, and remove or reauthenticate the integration.

Diagnostics intentionally omit cookie values, customer identifiers, meter
identifiers, consumption values, and raw server payloads. Internal device,
entity, and statistic identifiers use opaque SHA-256-derived values instead of
raw portal identifiers. Hashing is not encryption and predictable source IDs
may still be guessable. The meter number and custom portal name are
intentionally visible in the device name and serial-number field, as described
above.

See [Data, security, and privacy](docs/security-and-privacy.md) for the complete
data-flow and storage description. Security vulnerabilities must be reported
privately according to [SECURITY.md](SECURITY.md), never in a public issue.

## Troubleshooting

**Cookies are rejected immediately:** copy the request `Cookie` header from a
fresh authenticated `heartbeat` or `itemGroups` request. Confirm that it
contains both required cookies and that the portal URL has only the HTTPS host.

**No compatible meters are found:** the portal may expose an unknown unit,
view, or response schema. Download diagnostics from the integration page; they
contain counts and timestamps but no values or provider identifiers.

**Entities become unavailable:** check connectivity and Home Assistant logs.
Temporary failures are retried on the next coordinator refresh; an expired
session creates a reauthentication repair.

More setup, data, diagnostics, reauth, and ApexCharts scenarios are covered in
the [troubleshooting guide](docs/troubleshooting.md).

For temporary debug logging, add this to `configuration.yaml` and restart:

```yaml
logger:
  default: info
  logs:
    custom_components.energykey: debug
```

Remove debug logging after troubleshooting. Before sharing any log, search it
for cookies, tokens, addresses, customer details, and meter identifiers.

## Development

The repository includes a Dev Container with Python 3.14, Docker, GitHub CLI,
and a separate Home Assistant container. Docker and an editor with Dev
Container support are the only host prerequisites.

1. Open the repository and run **Dev Containers: Reopen in Container**.
2. Wait for the `dev` and `homeassistant` services to start.
3. Open forwarded port 8123 and complete Home Assistant onboarding.
4. Add EnergyKey under **Settings → Devices & services**.

GitHub CLI is available as `gh`. Authenticate it when repository operations are
needed; credentials are kept out of the image and repository:

```sh
gh auth login
gh auth status
```

The component directory is mounted read-only into the Home Assistant
container. Restart it after source changes:

```sh
docker compose -f .devcontainer/compose.yaml restart homeassistant
```

Run the local checks with:

```sh
ruff check .
ruff format --check .
pytest
python -m json.tool custom_components/energykey/manifest.json >/dev/null
python -m json.tool custom_components/energykey/translations/en.json >/dev/null
python -m json.tool custom_components/energykey/translations/da.json >/dev/null
```

The CI workflow also runs coverage, Home Assistant hassfest, HACS validation,
and secret scanning. HACS validation is read-only: no workflow in this
repository submits or pushes the integration to HACS' default catalog. Runtime
state is written under `.homeassistant/` and is excluded from Git.

Tests use synthetic, fully anonymized response fixtures. Live acceptance must
be performed locally with real cookies that are never committed: compare every
meter, unit, actual and expected value, heat volume, temperature, completed
period, and timestamp with the portal. Also verify the external
statistics across the available historical window; leave the session active
beyond 25 minutes; restart Home Assistant; then invalidate and reauthenticate
the session.

Development uses Gitflow with `main`, `develop`, feature branches, and release
branches. Beta and release-candidate tags create GitHub prereleases only. See
[RELEASING.md](RELEASING.md). Inclusion in HACS' default catalog is a separate
manual decision and is intentionally deferred.

## Removal

Removing a config entry also removes its stored cookies, normalized history,
and integration-owned external statistics. Read the [removal guide](docs/removal.md)
before deleting an entry or restoring an old backup.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
