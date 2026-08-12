# Entities and data

EnergyKey discovers active compatible meters. One Home Assistant device is
created per meter. The device name includes the utility type, an optional
portal name, and the meter number. The meter number is also visible as the
device serial number.

## Entity reference

| Entity | Meter | Unit | Enabled | Meaning | History attribute | External statistics |
| --- | --- | --- | --- | --- | --- | --- |
| Latest completed daily consumption | Water and heat | Portal water or energy unit | Yes | Newest portal period with `complete=true` | Up to 13 months | Actual values only |
| Expected daily consumption | Water and heat when available | Same class as consumption | Yes | Portal `usageBudget` reference value | Up to 13 months | No |
| Latest completed daily heat volume | Heat when available | Recognized volume unit | Yes | Volume transported during the newest complete heat period | Up to 13 months | Yes |
| Latest flow temperature | Heat when available | °C or °F | Yes | Temperature value matching the newest complete heat period | Up to 13 months | No |
| Latest return temperature | Heat when available | °C or °F | Yes | Temperature value matching the newest complete heat period | Up to 13 months | No |
| Last successful session renewal | Integration | Timestamp | No | Latest successful startup-heartbeat or scheduled consumption-keepalive pass; the underlying `last_successful_heartbeat` key remains unchanged | No | No |

The daily values are historical aggregates, not live measurements. Data may be
several days old. The integration does not expose a physical meter-reading
entity because the currently verified portal views do not show a reliable
meter reading.

## Historical attribute

Each metric sensor exposes an ordered `data` attribute:

```yaml
data:
  - start: "2026-07-30T22:00:00+00:00"
    end: "2026-07-31T22:00:00+00:00"
    value: 0.31
    complete: true
```

`complete=false` means EnergyKey has not finalized the period. Such points are
retained so a later refresh can revise them, but they do not determine the
sensor state and are not imported as measured statistics.

Temperature series do not provide a reliable completion flag. Their periods
are considered complete only when the same period is complete in actual heat
consumption. Recency is never used as a substitute for portal completion.

The `data` attribute is excluded from Recorder. This avoids storing the full
13-month array on every state change. Dashboards can still read the current
attribute in the frontend.

## Backfill and Recorder

Complete actual water consumption, heat consumption, and heat volume are
imported as external long-term statistics. The initial refresh can backfill up
to 13 calendar months. The sensor entities' ordinary state history begins when
the integration is installed.

The newest 45 days are fetched again during a full reconciliation every 6
hours. Portal corrections replace existing statistics at the same timestamps
rather than adding duplicates. Expected values and temperatures are not
imported as measured consumption.

Between full reconciliations, each meter's primary consumption view is queried
after 20 minutes without a successful consumption request for that meter. This
lightweight keepalive uses a seven-day lookback through the current day. If it
contains a new or revised complete actual- or expected-consumption point, the
integration starts a normal refresh for all views. Incomplete or unchanged
points do not trigger that extra refresh; the six-hour reconciliation remains
the fallback for delayed values and corrections.

External statistic IDs start with `energykey:` and use opaque SHA-256-derived
meter keys. They are not physical lifetime meter readings. Removing the config
entry removes statistics owned by that entry; see [Removal](removal.md).

## Unsupported data

Unknown units and ambiguous internal series are skipped instead of guessed.
Cooling, billing, prognosis, and write operations are unsupported. Portal
support is currently verified against the Din Forsyning EnergyKey tenant; other
hosts below `*.wt.energykey.dk` should be treated as experimental until their
sanitized schema has been tested.
