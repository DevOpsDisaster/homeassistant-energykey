# Release process

This repository uses Gitflow and Semantic Versioning. The release automation
creates GitHub Releases only. It does not submit or push this integration to
the HACS default repository.

## Long-lived branches

- `main` contains only stable releases. Every commit on `main` intended for
  distribution has a matching stable tag such as `v0.1.0`.
- `develop` contains changes accepted for the next release.

Protect both branches on GitHub. Require pull requests, the Validate workflow,
resolved conversations, and prevention of force pushes and branch deletion.

Before the first public release, configure the repository's GitHub settings:

- description: `Unofficial read-only EnergyKey integration for Home Assistant`;
- topics: `home-assistant`, `hacs`, `energykey`, `water`, `district-heating`,
  and `denmark`;
- Issues and private vulnerability reporting enabled;
- secret scanning and push protection enabled where GitHub offers them;
- Dependabot alerts enabled.

## Working branches

- `feature/<name>` branches from and merges into `develop`.
- `fix/<name>` branches from and merges into `develop`.
- `release/<version>` branches from `develop`, for example `release/0.1.0`.
- `hotfix/<version>` branches from `main` and merge into both `main` and
  `develop`.

Version bumps are made only on release and hotfix branches. Keep an
**Unreleased** section in `CHANGELOG.md` while features are developed.

## Beta and release-candidate builds

1. Create `release/0.1.0` from `develop`.
2. Set `manifest.json` to `0.1.0-beta.1`, update the changelog, and run all
   checks.
3. Tag that commit `v0.1.0-beta.1` and push the tag. The release workflow
   verifies the tag and manifest match, reruns validation, and creates a
   prerelease on GitHub.
4. Apply release fixes on the same release branch. Increment to
   `0.1.0-beta.2` as needed.
5. When the feature set is complete, use `0.1.0-rc.1` and tag
   `v0.1.0-rc.1`. Additional candidates increment the final number.

Prereleases can be installed by testers who have explicitly added this repo as
a HACS custom repository. They do not add the project to HACS' default list.

## Stable release

1. Complete the live acceptance checklist in the README.
2. Set the manifest version to `0.1.0` and turn the changelog's planned version
   into the final release notes.
3. Merge `release/0.1.0` into `main` through a pull request.
4. Create the signed or annotated tag `v0.1.0` on the merge commit and push it.
5. Confirm the GitHub release workflow succeeds and the generated release is
   not marked as a prerelease.
6. Confirm the successful Validate run on `main` triggers the
   `Sync main to develop` workflow and merges the release back into `develop`.
   Resolve a failed sync manually if the branches conflict, then remove the
   release branch.

Do not create a PR against `hacs/default`. Inclusion in HACS' default catalog
is a separate, deliberate future decision. It requires an explicit manual PR
owned by a maintainer and is not part of any workflow in this repository.

While that submission is deferred, the HACS validation workflow ignores only
the `topics` and `description` default-catalog metadata checks. Before a future
`hacs/default` PR, configure the metadata, remove both ignores from the
validation and release workflows, and require a completely clean HACS Action
run with no ignored checks.

## Release checklist

- [ ] Manifest, tag, changelog, and documentation use the same version.
- [ ] Ruff, formatting, pytest, coverage, Hassfest, HACS validation, and
      Gitleaks pass on the exact tagged commit.
- [ ] The declared minimum and current stable Home Assistant versions load the
      integration.
- [ ] Fixtures, diagnostics, logs, screenshots, and Git history contain no
      session or customer data.
- [ ] Setup, initial background load, heartbeat, restart, 401/403, reauth,
      delayed data, revisions, backfill, and removal have been exercised.
- [ ] Every supported meter and unit has been compared with the portal.
- [ ] Known limitations and any provider schema changes are documented.
- [ ] The neutral brand assets render correctly in light and dark themes.
- [ ] GitHub description, topics, Issues, private vulnerability reporting,
      branch protection, and security features are configured.
