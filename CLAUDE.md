# setup

- Structure, contracts and the rules not to break: `doc/ARCHITECTURE.md` (§9). User guide (Lithuanian): `doc/lt/`.
- Terms: `GLOSSARY.md`. Decisions not to re-open: `doc/adr/`.
- A PrestaShop shop's .gitignore (one, at the shop root, by explicit paths in five groups; what is code and what is
  content): `doc/lt/06-aplikacija.md` "Parduotuvės .gitignore", ADR 2.
- Tests: `tests/run` exits with the suite's status and names the failing tests (`--help` for the B levels:
  `--database mysql:8.4|mariadb:11.4`, `--release`). Check its exit status before committing; output piped
  through `tail` or `grep` hides it.
- A change to Docker logic updates `doc/` with it: `ARCHITECTURE.md`, `RELEASES.md`, and `doc/lt/` where users see it.
- Every merge to master releases itself after CI (`.github/workflows/release.yml`, `tools/release.py`): add the entry
  under `## Unreleased` in `doc/RELEASES.md` and mark the heading `(minor)` or `(major)` when it is more than a patch.
  Never bump `VERSION` or tag by hand.
