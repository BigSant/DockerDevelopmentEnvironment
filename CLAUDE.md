# setup

- Structure, contracts and the rules not to break: `doc/ARCHITECTURE.md` (§9). User guide (Lithuanian): `doc/lt/`.
- Terms: `GLOSSARY.md`. Decisions not to re-open: `doc/adr/`.
- Tests: `tests/run` exits with the suite's status and names the failing tests (`--help` for the B levels:
  `--database mysql:8.4|mariadb:11.4`, `--release`). Check its exit status before committing; output piped
  through `tail` or `grep` hides it.
- A change to Docker logic updates `doc/` with it: `ARCHITECTURE.md`, `RELEASES.md`, and `doc/lt/` where users see it.
