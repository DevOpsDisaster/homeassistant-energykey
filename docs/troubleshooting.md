# Troubleshooting

## Setup and authentication

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Invalid Cookie header | Missing `wt3SessionId` or `wt3login`, a response `Set-Cookie` header was copied, or the session expired | Copy the request `Cookie` header from a fresh authenticated `heartbeat` or `itemGroups` request |
| Cannot connect | DNS, firewall, TLS, portal outage, or timeout | Test the portal from the Home Assistant network and retry later |
| Unsupported response | Portal schema, content type, unit, or view is unknown | Download redacted diagnostics and report only sanitized schema characteristics |
| Already configured | The same portal account already has a config entry | Reauthenticate or remove the existing entry instead of adding a duplicate |
| Wrong account during reauth | Fresh cookies belong to a different account | Sign into the original account or remove the entry to intentionally switch accounts |

## Missing or unavailable data

No entity is created until its live series and unit have been verified. A
missing expected, volume, or temperature entity usually means that the portal
does not expose the corresponding stable internal series for that meter.

Temporary update failures leave the last valid coordinator data in memory but
mark entities unavailable. A definitive 401, 403, false heartbeat, or login
redirect stops further session requests and creates a Home Assistant reauth
repair.

Initial meter discovery should complete quickly. Historical loading continues
in the background and can take longer. Failed old month chunks are retried on
later daily refreshes.

## Diagnostics

Open **Settings > Devices & services > EnergyKey**, open the integration menu,
and choose **Download diagnostics**. Diagnostics intentionally omit cookies,
customer and meter identifiers, raw values, and provider payloads.

Inspect the file anyway before sharing it. If an unknown provider schema must
be investigated, create a manually minimized synthetic fixture rather than
publishing a raw response.

## Debug logging

Temporarily add:

```yaml
logger:
  default: info
  logs:
    custom_components.energykey: debug
```

Restart Home Assistant and reproduce the issue once. Remove debug logging
afterward. Manually search logs for cookies, tokens, addresses, customer data,
meter identifiers, and consumption values before sharing anything.

## ApexCharts download or card errors

`Timeout while contacting DNS servers` for GitHub release assets is a Home
Assistant container DNS/network problem. It is not caused by EnergyKey. Check
container DNS, firewall rules, proxy settings, and access to GitHub, then retry
the HACS frontend download.

`Custom element doesn't exist: apexcharts-card` means the frontend resource is
not installed, registered, or loaded. Follow [Dashboard guide](dashboard.md),
then hard-refresh the browser.
