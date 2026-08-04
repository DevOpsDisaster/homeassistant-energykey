# Cookie authentication

EnergyKey does not provide a supported authentication API for this
integration. Sign in normally in a browser, then copy the `Cookie` request
header from an authenticated EnergyKey request. Treat the value exactly like a
password.

Paste it only into the EnergyKey config or reauthentication form in your own
Home Assistant instance. Never paste it into YAML, an issue, a chat, a
screenshot, a log, or a fixture.

## Chrome and Edge

1. Open the configured EnergyKey portal and sign in.
2. Open Developer Tools with `F12` or **Inspect**, then select **Network**.
3. Reload the portal page.
4. Select an authenticated `heartbeat` or `itemGroups` request.
5. Open **Headers**, locate **Request Headers**, and find `Cookie`.
6. Copy the complete value. If the browser provides a source/raw view, copying
   the complete `Cookie: ...` line is also supported.

Do not use a response `Set-Cookie` header. Do not publish a “Copy as cURL”
command; it can contain cookies and other identifying request data.

## Firefox

1. Open the EnergyKey portal and sign in.
2. Open **Web Developer Tools > Network** and reload the page.
3. Select an authenticated `heartbeat` or `itemGroups` request.
4. Open **Headers > Request headers**.
5. Copy the entire value beside `Cookie`. Firefox's raw headers view can be
   used when the normal table truncates the display.

Browser labels can change between releases. The important distinction is that
the value comes from an authenticated request sent to the exact configured
EnergyKey host.

## Home Assistant setup

1. Keep the default portal URL or enter the exact tenant root, for example
   `https://dinforsyning.wt.energykey.dk`.
2. Do not include a path, query, fragment, username, password, or non-default
   port.
3. Paste the Cookie value or complete `Cookie: ...` line.
4. Submit the form. The integration requires `wt3SessionId` and `wt3login`,
   validates the session using heartbeat and meter discovery, and stores the
   resulting cookies locally in Home Assistant.

## Reauthentication

A 401, 403, false heartbeat, or login redirect stops heartbeat attempts and
creates a Home Assistant repair. Sign in to the same EnergyKey account again,
repeat the browser steps, and paste the fresh Cookie header into the repair
form. A different account is rejected to preserve device and entity identity.

The private EnergyKey API does not provide a documented independent account
identity endpoint. Same-account validation therefore relies on stable metadata
from the newly server-validated login session. Test account switching carefully
when contributing support for another tenant schema.

## Revocation

Signing out may invalidate the browser session, but provider behavior can
vary. Use an active-session revocation control when the portal offers one.
Removing or reauthenticating the Home Assistant config entry replaces local
session state; it does not by itself guarantee server-side revocation.
