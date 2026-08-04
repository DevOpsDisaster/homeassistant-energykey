## Summary

Describe the user-visible change and why it is needed.

## Validation

- [ ] Ruff and formatting pass.
- [ ] Tests pass and coverage does not decrease without justification.
- [ ] Hassfest passes.
- [ ] HACS validation passes.
- [ ] New or changed UI text exists in both English and Danish translations.
- [ ] Documentation and examples are updated where necessary.

## Security and privacy

- [ ] No cookies, tokens, customer data, addresses, meter identifiers, or raw live payloads are included.
- [ ] Fixtures and screenshots are synthetic and sanitized according to `CONTRIBUTING.md`.
- [ ] Logs and diagnostics remain redacted.
- [ ] Network changes remain read-only and restricted to the configured EnergyKey origin.

## Release impact

- [ ] The change is compatible with the current config-entry and storage schema, or includes a migration.
- [ ] `CHANGELOG.md` contains an entry under **Unreleased**.
