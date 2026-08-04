# Removal

Removing EnergyKey is intentionally destructive for integration-owned local
state.

1. Open **Settings > Devices & services > EnergyKey**.
2. Open the config-entry menu and choose **Delete**.
3. Confirm the deletion.

The integration then stops polling and heartbeat tasks, closes its private
session, deletes its stored cookies and normalized history, and queues removal
of its external statistics. Devices and entities belonging only to that config
entry are removed by Home Assistant.

To revoke the portal session as well, sign out of EnergyKey and invalidate
active sessions if the portal provides that control. Removing local files does
not guarantee that the provider has revoked a copied browser session.

After deleting the config entry, the integration files can be removed through
HACS. For a manual installation, stop Home Assistant, delete only
`/config/custom_components/energykey`, and start Home Assistant again.

Do not delete the entire `custom_components` or `.storage` directory.

If you may need the historical statistics later, export or back up the needed
data before deleting the config entry. Restoring a Home Assistant backup can
also restore old EnergyKey cookies, so revoke restored sessions when the backup
is no longer trusted.
