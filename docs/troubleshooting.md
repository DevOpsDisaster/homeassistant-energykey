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
mark entities unavailable. A definitive 401, 403, false startup heartbeat, or
login redirect stops further session requests and creates a Home Assistant
reauth repair.

EnergyKey also maintains a consumption context for each individual meter item.
A successful generic heartbeat or a request for another meter does not renew
that context. The integration therefore queries each meter's primary view after
20 minutes without consumption activity, using a seven-day lookback through the
current day. If EnergyKey responds with error `1101` because an item or view
context has become stale, rediscovery cannot repair the existing session and
the integration starts reauthentication.

Normal refresh requests retry network errors, timeouts, and ordinary HTTP 5xx
responses up to three times with a short exponential delay. A lightweight
20-minute consumption keepalive uses one request attempt; its loop retries a
transient failure after one minute and respects bounded `Retry-After` guidance.
If attempts still fail, the coordinator keeps the last valid data. Normal logs
identify the HTTP method, endpoint, status, and attempt number. Debug logs also
include up to 2 KiB of an HTTP 5xx response body, with control characters
escaped and a truncation marker when needed. Query parameters, request bodies,
and cookies are never logged. Treat debug response excerpts as potentially
sensitive and inspect them before sharing.

Initial meter discovery should complete quickly. Historical loading continues
in the background and can take longer. Failed old month chunks are retried on
later full reconciliations, which run every 6 hours and always revisit the
newest 45 days. The 20-minute primary-view keepalive also acts as a change
detector: a new or revised complete point starts a normal refresh immediately,
while unchanged or incomplete points do not.

During a graceful Home Assistant shutdown, EnergyKey sends one final heartbeat
with a five-second deadline and persists any rotated cookies. Startup also
sends a heartbeat before meter discovery. These heartbeat calls help the
cookie session but do not replace the per-meter consumption keepalives. The
shutdown call cannot run after a crash, forced kill, or power loss, and it
cannot preserve an item-scoped context through a long shutdown.

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
