# Live acceptance

Run this checklist on the exact commit intended for a prerelease or stable
release. Use a dedicated local Home Assistant test instance and a real portal
session, but record only categorical outcomes. Never record screenshots, raw
responses, identifiers, meter numbers, addresses, cookies, tokens, or measured
values.

1. Copy `tests/fixtures/live_acceptance.json` outside the repository and set the
   release, Home Assistant version, UTC test time, and a non-identifying portal
   profile label.
2. Exercise every scenario listed in the record. Confirm entity and statistic
   behavior visually against the portal, then set the outcome to `pass`.
   `not_applicable` is permitted only when the tenant lacks that capability.
3. Validate the completed record:

   ```sh
   python scripts/validate_live_acceptance.py /safe/path/acceptance.json
   ```

4. Attach the validated record to the private release evidence or summarize its
   pass status in the release PR. Do not commit a record produced from a live
   household account.

The validator enforces the complete lifecycle checklist and rejects common
sensitive field names. This is a guardrail, not anonymization: inspect the file
manually before sharing it.
