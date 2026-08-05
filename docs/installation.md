# Installation

## Prerequisites

- Home Assistant 2026.7.0 or newer.
- A working EnergyKey account on an HTTPS host below `*.wt.energykey.dk`.
- A browser session that can open the authenticated portal.
- HACS for custom-repository installation, or filesystem access for manual
  installation.

The integration is not listed in HACS' default catalog. Adding it as a custom
repository is a local user choice and does not submit the project to HACS.

## HACS custom repository

1. Open **HACS > Integrations**.
2. Open the menu and choose **Custom repositories**.
3. Enter `https://github.com/DevOpsDisaster/homeassistant-energykey`.
4. Select **Integration** as the category and add the repository.
5. Open **EnergyKey**, choose the desired published version, and download it.
6. Restart Home Assistant.
7. Open **Settings > Devices & services > Add integration**, search for
   **EnergyKey**, and continue with [Cookie authentication](cookie-authentication.md).

For beta and release-candidate testing, deliberately select the corresponding
GitHub prerelease when HACS offers it. Do not deploy prereleases where utility
data availability is operationally critical.

## Manual installation

1. Download a GitHub Release from this repository. Avoid arbitrary branch
   archives for normal installations.
2. Copy the contained `custom_components/energykey` directory into the Home
   Assistant configuration directory.
3. Confirm the final path is
   `/config/custom_components/energykey/manifest.json` and not nested inside a
   second `energykey` directory.
4. Restart Home Assistant.
5. Add **EnergyKey** from **Settings > Devices & services**.

## Setup behavior

Configuration validates the portal and cookies, discovers active meters and
their views, and then returns. Current and 13-month historical data load in the
background, so sensors may appear shortly after their devices.

The integration creates one device per compatible meter. Water and heat are
shown separately, with the portal name and meter number when available. A
missing optional entity means its data series or unit was not verified for
that meter.

## Upgrade

Back up Home Assistant before upgrading a prerelease or a version that changes
the config-entry or storage schema. With HACS, select the new release and
restart when prompted. Manual installations must replace the complete
`custom_components/energykey` directory; do not mix files from two versions.

Never copy `.storage` files between installations as an upgrade method. They
can contain live EnergyKey session cookies.
