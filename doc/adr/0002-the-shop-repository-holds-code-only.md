# 2. The shop repository holds code only

Status: accepted (2026-10-28)

## Context

A PrestaShop shop repository mixes three kinds of files: the shop's code, what the official release ships unchanged,
and what the server writes (content, caches, generated files). The shops moved into setup (forsena, anodas) had
.gitignore files written over the years with rules by file type and folder name (`*.png`, `*.svg`, `cache/`, `log/`,
`.htaccess`, `composer.lock`) plus each module author's own .gitignore. Checked against full LIVE copies, those rules
hid code a clean clone needs: 1083 files in anodas (module `vendor/psr/log`, deny `.htaccess` files, theme and module
images, module `composer.lock`), our own `ets_translate` tests, and a module's mail templates. At the same time they
tracked what the server owns: module translations the back office edits, and a GDPR export with customer data.

## Decision

- The shop's root .gitignore is its only one (a pre-commit check stops another; `vendor/` packages keep theirs). It
  ignores by explicit path in five groups: release, generated, content, tools, single modules (by who makes them).
  No rule by file type or folder name anywhere, except tool state that means the same everywhere (`.idea/`,
  `node_modules/`).
- Code, tracked: module `vendor/`, tests and fixtures, mail templates (`mails/`, module and theme mails; not the subject
  files `mails/<iso>/lang.php`), module
  guards no release ships, images and fonts of themes and modules, overrides, core code outside `PS_RELEASE_PATHS`.
- Not tracked:
  - the release's own files (`PS_RELEASE_PATHS`; the guards of `img/`, `upload/`, `download/`, `cache/`, which setup
    copies into those protected folders when missing, so the folders are ignored whole);
  - content the server writes: module translations, mail subjects `mails/<iso>/lang.php`, language packs `translations/<locale>/` (pack installs in the
    back office, translation modules such as `ets_translate`), `img/` content, uploads, invoices, module data;
  - generated files: `config*.xml` of modules, `var/`, sitemaps, build output (`config/deploy/build.sh` builds it in
    every release);
  - secrets (`parameters.php`, a module's `credentials.json`).
- A translation developers make travels as a setup translation migration (database rows, legacy module files, mail
  subject files), not as
  a language pack file.

## Consequences

- A clean clone plus `make setup` runs the shop; LIVE files the repository does not hold are content and stay on the
  server (a setup deploy never writes or deletes protected paths; setup's files backups copy the translations).
- Do not reintroduce rules by extension or folder name, nor track language packs or `config.xml`: each was tried and
  each hid or overwrote the wrong files. A new module's .gitignore is merged into the root one, never kept.
- Untracking a file the repository holds is safe only once the server deploys with setup: a `git checkout -f`
  deploy deletes it on the server.
- Mail templates stay code: a module ships them; translating them on the server (`ets_translate`) means committing
  the result.
