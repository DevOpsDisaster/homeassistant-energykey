# Home Assistant EnergyKey

Et udviklingsskelet til en EnergyKey custom integration, som kan distribueres
med HACS.

## Udvikling med Dev Containers

Forudsætningerne er Docker og en editor med understøttelse af Dev Containers,
for eksempel VS Code med Dev Containers-udvidelsen.

1. Åbn repositoryet i din editor. Det fungerer også i et Coder-workspace, når
   workspacet stiller Docker til rådighed.
2. Kør **Dev Containers: Reopen in Container**.
3. Vent på at `dev` og `homeassistant` er startet af Docker Compose.
4. Åbn den forwardede **Home Assistant**-port (8123), gennemfør onboarding,
   og tilføj derefter EnergyKey under **Settings → Devices & services**.

Integrationens mappe bind-mountes fra
`custom_components/energykey` direkte ind i Home Assistant-containeren. Efter
kodeændringer kan Home Assistant genstartes fra terminalen:

```sh
docker compose -f .devcontainer/compose.yaml restart homeassistant
```

Logs og statiske checks:

```sh
docker compose -f .devcontainer/compose.yaml logs -f homeassistant
ruff check .
ruff format --check .
```

Home Assistants lokale runtime-data ligger i `.homeassistant/`; kun
`configuration.yaml` versionsstyres. Image-tag og host-port kan overskrives
med henholdsvis `HOME_ASSISTANT_TAG` og `HOME_ASSISTANT_PORT`.
