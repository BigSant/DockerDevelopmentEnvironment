# Shared setup releases

`VERSION` identifies the setup release. `make setup-info` reports version, API,
Git revision and whether the checkout has local modifications.
`SETUP_REQUIRED_API=1` in a project states the supported runner API; mismatches
fail before Docker mutations. Share one setup checkout; no submodules are needed.

New grouped projects set `VERSIONED_IMAGES=1`. Locally built image names then
include a fingerprint of shared Docker build sources/defaults, resolved build
arguments and the application profile. A changed Dockerfile, config, profile or
build argument (such as Node version) gets a different tag rather than
replacing another project's existing shared image. Local and test stacks reuse
the same image identity. Project-specific PHP images retain their project prefix.
External images such as optional Redis retain their explicit version tags.
Existing projects without this setting retain their old image names until they
opt in and build the corresponding namespaced images.

The fingerprint prevents local tag collisions; it does not make upstream base
tags immutable or establish image provenance. A reproducible production release
should additionally pin upstream digests and build/publish its own tested images.
Do not delete previous image tags or update every working checkout during a release.

The GitHub workflow tests source handling, configuration preparation on PHP
5.6/7.4/8.1/8.5 and disposable MySQL 5.7/8.4/MariaDB 11.4 databases. DB tests cover
schema drift checks, fixtures, compressed backup/restore and import ordering.
PHP 5.6 coverage validates the PS 1.6 configuration updater; it is not a claim
that every PrestaShop version runs against every matrix DB/PHP combination.

Release procedure:

1. Run unit and integration checks; verify a fresh local/test setup and an existing
   project migration. Wait for all CI jobs to pass on the exact commit.
2. Update VERSION/docs and API compatibility when required. Record migration
   steps for renamed commands or path defaults.
3. Tag that tested commit `v<version>`. Use the tag for coordinated adoption;
   keep the previous tag/images available for rollback.
4. Each machine updates its one shared checkout to the chosen tag, then runs
   `make setup-info`, `make check`, `make doctor` and `make up` for its projects.

CI also runs on version tags. VERSION alone is not proof that CI passed. No
release tag or hosted release is published automatically by these scripts.
