# Changelog

All notable user-visible changes are documented here. The project follows
[Semantic Versioning](https://semver.org/) with `-beta.N` and `-rc.N`
prereleases.

## Unreleased

## 0.1.0-rc.4 - 2026-08-12

### Changed

- Replaced the periodic generic heartbeat with a lightweight primary-
  consumption keepalive for each meter after 20 minutes without consumption
  activity. The seven-day lookback response also detects new or revised
  complete points and starts a normal refresh only when needed; full 45-day
  reconciliation continues every 6 hours.
- Stale item-scoped EnergyKey consumption contexts now start reauthentication,
  while the startup and graceful-shutdown heartbeats remain in place.
- Renamed the diagnostic display label to **Last successful session renewal**
  while retaining its existing registry key and entity identity.

## 0.1.0-rc.3 - 2026-08-07

### Fixed

- Rediscover meters and consumption views once when EnergyKey rejects a cached
  item or view, while preserving stable meter identity and stored history.

## 0.1.0-rc.2 - 2026-08-07

### Changed

- Refresh recent consumption data every 6 hours and make heartbeat diagnostics
  account-specific while preserving existing entity identity.
- Send a startup heartbeat before discovery and a bounded best-effort heartbeat
  during graceful shutdown.

### Fixed

- Retry transient network and HTTP 5xx failures with bounded backoff and log
  bounded, escaped server-error excerpts at debug level.

## 0.1.0-rc.1 - 2026-08-05

### Added

- Publication, contribution, security, troubleshooting, dashboard, and
  removal documentation.
- Secret scanning, dependency updates, pinned GitHub Actions, and a GitHub-only
  release workflow.
- Bounded cookie and response parsing plus explicit rate-limit handling.

### Changed

- Historical daily temperature sensors no longer use the present-time
  `measurement` state class.
- Privacy documentation now describes opaque SHA-256-derived identifiers
  without claiming they are impossible to reverse.

## 0.1.0

Initial planned public release. This section remains unreleased until the
stable `v0.1.0` GitHub Release is created.
