# Security policy

EnergyKey for Home Assistant handles browser session cookies that may grant
access to private utility data. Treat every EnergyKey Cookie header as a
password.

## Reporting a vulnerability

Do not report vulnerabilities in a public issue. Use a
[private GitHub security advisory](https://github.com/DevOpsDisaster/homeassistant-energykey/security/advisories/new)
and include only the minimum information needed to reproduce the problem.

Never send a real Cookie header, login token, customer identifier, address,
meter identifier, or raw portal response. Use synthetic replacements. If a
credential was exposed, sign out of the portal, revoke active sessions when
the portal supports it, and replace the Home Assistant integration session.

The maintainer will acknowledge a valid report, investigate it privately, and
coordinate a fixed release before public disclosure. No response-time SLA is
promised for this community-maintained project.

## Supported versions

Security fixes are provided for the latest published release. Users of older
versions should upgrade before reporting a problem that is already fixed in a
newer release.

## Security boundaries

- Cookies and normalized history are stored locally in Home Assistant's
  `.storage` directory. The integration does not add encryption at rest.
- Anyone who can read the Home Assistant configuration directory or backups
  may be able to recover the active portal session.
- Requests use HTTPS and are restricted to configured hosts below
  `*.wt.energykey.dk`. Cross-origin redirects are rejected.
- The integration is read-only and must not add write operations, MitID
  automation, or browser automation without a separate security review.
- EnergyKey uses private, unsupported endpoints. Provider-side changes and
  availability are outside the maintainer's control.

See [Data, security, and privacy](docs/security-and-privacy.md) for user-facing
storage, diagnostics, and removal details.
