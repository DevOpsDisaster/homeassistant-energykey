# Contributing

Contributions are welcome through feature branches and pull requests into
`develop`. Read [RELEASING.md](RELEASING.md) before changing versions or
creating tags.

## Development checks

Use the included Dev Container or Python 3.14, then run:

```sh
ruff check .
ruff format --check .
pytest --cov=custom_components.energykey --cov-report=term --cov-fail-under=90
python scripts/validate_live_acceptance.py tests/fixtures/live_acceptance.json
```

GitHub Actions additionally runs Hassfest, HACS validation, and Gitleaks. HACS
validation is read-only; it does not submit the integration to HACS.

## Live data and fixtures

Never commit a raw browser capture or live API response. Build the smallest
fixture needed for the test, then replace all of the following before saving
it:

- cookies, login state, session IDs, handoff tokens, and authorization data;
- customer, context, user, item, view, meter, and database identifiers;
- names, addresses, supply locations, account numbers, and meter numbers;
- consumption, counter, temperature, billing, and expected values that came
  from a real household.

Use obviously synthetic identifiers such as `synthetic-water-meter` and
invented values. Search the complete change for every original value before
committing. Do not rely on diagnostics redaction to sanitize fixtures.

Screenshots must use synthetic Home Assistant entities and device names. Crop
browser chrome and notifications when they may expose names, hosts, account
information, or other integrations.

## Code expectations

- Keep all production code, comments, logs, documentation, and canonical UI
  text in English.
- Keep `translations/en.json` and `translations/da.json` complete and aligned.
- Do not add `strings.json`; custom integrations load complete translation
  files directly.
- Use Home Assistant async APIs and config-entry lifecycle helpers.
- Add tests for success, authentication failure, transient failure, malformed
  data, unloading, and redaction when applicable.
- Never log request headers, cookies, raw payloads, item IDs, meter IDs, or
  customer-specific URL parameters.
- Preserve entity, device, and external-statistic identifiers across upgrades.
- Add an **Unreleased** changelog entry for user-visible changes.

## Pull requests

Branch from `develop` using `feature/<short-name>` or `fix/<short-name>`. Keep
the pull request focused and complete the security checklist in the template.
Do not bump the manifest version in feature branches; version changes belong
on a `release/<version>` or `hotfix/<version>` branch.
