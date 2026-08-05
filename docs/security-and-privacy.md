# Data, security, and privacy

## Data flow

The integration sends read-only HTTPS requests from Home Assistant to the
configured EnergyKey tenant below `*.wt.energykey.dk`. It does not send
telemetry to the project maintainer and does not contact HACS, GitHub, or an
analytics service at runtime.

The manually copied Cookie header is used to bootstrap a private HTTP session.
Heartbeat requests keep the server session active; consumption requests run
after setup and then daily.

## Locally stored data

Home Assistant stores bootstrap cookies in config-entry data. Rotated session
cookies, normalized history, and statistic offsets are stored in an
entry-specific Home Assistant Store file. These files are inside `.storage`
and are not encrypted by this integration.

The integration retains up to 13 calendar months of normalized history and
re-fetches the latest 45 days. Home Assistant Recorder separately owns the
external long-term statistics imported by the integration.

Protect the Home Assistant host, configuration directory, backups, support
archives, and administrator accounts. Anyone with access to them may be able
to read private utility data or recover a live portal session.

## Identifiers visible in Home Assistant

Registry and statistic identifiers use opaque SHA-256-derived values rather
than raw EnergyKey item IDs. Hashing reduces accidental disclosure but is not
encryption and does not make predictable source identifiers impossible to
guess.

The portal's custom meter name and meter number are intentionally visible in
the Home Assistant device name, and the meter number is registered as its
serial number. They can therefore appear in UI screenshots and Home Assistant
registry exports even though diagnostics omit them.

## Diagnostics and logs

Diagnostics omit cookies, account IDs, meter IDs, raw payloads, consumption
values, addresses, and meter numbers. Logs use generic protocol errors and
must never include request headers, form bodies, or raw responses.

Users must still inspect diagnostics, logs, and screenshots before sharing
them. Report a suspected leak through the private process in
[SECURITY.md](../SECURITY.md).

## Revocation and deletion

Reauthentication replaces the local session while preserving entity and
statistic identity. Removing the config entry deletes its cookie store,
normalized history, and integration-owned external statistics.

Local deletion and server revocation are separate. Sign out of EnergyKey and
invalidate active sessions when available. A restored backup may contain an
older valid cookie and should be treated accordingly.
