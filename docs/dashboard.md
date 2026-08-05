# Dashboard guide

EnergyKey works without a custom dashboard card. The included examples use the
third-party ApexCharts Card because it can render the integration's historical
`data` attributes.

## Install ApexCharts Card

1. Open **HACS > Frontend**.
2. Search for **ApexCharts Card** by RomRider and download it.
3. Restart or hard-refresh the Home Assistant frontend when HACS asks.
4. Open **Settings > Dashboards > Resources** and confirm that
   `/hacsfiles/apexcharts-card/apexcharts-card.js` is registered as a JavaScript
   module. HACS normally creates this resource automatically.

If download fails with a DNS timeout for `release-assets.githubusercontent.com`,
fix DNS/network access from the Home Assistant container before retrying. This
is independent of EnergyKey. A manually downloaded frontend resource should be
used only when its release and checksum have been verified.

## Find the entity IDs

1. Open **Settings > Devices & services > EnergyKey**.
2. Select the water or heat device.
3. Open each relevant entity and copy its entity ID from its settings.

The optional heartbeat entity is disabled by default and is not needed by the
charts.

## Add a card

1. Open the target dashboard and choose **Edit dashboard**.
2. Add a **Manual** card. In a Sections dashboard, add the manual card inside
   the desired section.
3. Copy one of the examples:
   - [Daily water, heat, and temperatures](../examples/apexcharts-water-and-heat.yaml)
   - [Cumulative actual and expected consumption](../examples/apexcharts-cumulative-water-and-heat.yaml)
4. Replace every `REPLACE_*` placeholder with the copied entity ID.
5. Save and verify the legend, units, and latest completed period against the
   EnergyKey portal.

All example filters use `point.complete`. They do not assume that today's or
yesterday's data exists. This is important because utility data can be delayed
by several days.

The cumulative charts begin at zero at the first complete period inside the
displayed range. They show consumption accumulated within that range, not the
meter's physical lifetime counter.

## Built-in Home Assistant alternative

Actual consumption and heat volume are also available as external long-term
statistics. Add a built-in **Statistics graph** card and select an ID beginning
with `energykey:`. This avoids a frontend dependency but does not plot expected
consumption or the full sensor attribute in the same way as ApexCharts.

## Sharing screenshots

Before publishing a screenshot, replace or crop device names, meter numbers,
entity IDs, addresses, dashboard URLs, and unrelated notifications. Prefer a
dashboard populated from synthetic fixtures.
