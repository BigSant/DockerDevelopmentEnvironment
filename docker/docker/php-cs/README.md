# PHP CS Fixer

## What is PHP CS Fixer?

[PHP CS Fixer](https://cs.symfony.com/) (PHP Coding Standards Fixer) is a tool that automatically fixes PHP code to follow defined coding standards. It was originally created by Fabien Potencier and is now maintained by the PHP CS Fixer community.

Unlike linters that only report problems, PHP CS Fixer **actively rewrites your source files** to conform to the configured rules — no manual editing required.

### What it fixes

- Indentation, spacing and blank lines
- Brace placement and bracket style
- Import ordering and unused `use` statements
- PHPDoc formatting
- Visibility declarations (`public`, `protected`, `private`)
- Return type declarations and type hints
- And hundreds of other rules covering PSR-1, PSR-2, PSR-12, Symfony, and custom rule sets

### Why use it?

- Enforces a consistent code style across the entire team automatically
- Eliminates style-related review comments
- Can be run as a pre-commit hook or in CI/CD pipelines
- Reduces cognitive overhead when reading code written by different developers

---

## Service overview

The `php-cs` service runs PHP CS Fixer inside a dedicated Docker container based on the official [`ghcr.io/php-cs-fixer/shim`](https://github.com/PHP-CS-Fixer/shim/pkgs/container/shim) image.

The version is controlled by `PHP_CS_VERSION` in `.env`.

### Volumes

| Host path | Container path | Purpose |
|---|---|---|
| `PROJECT_WEB_DIRECTORY` | `/var/www/html` (read-only) | Project source code |
| `PROJECT_CONFIG_DIRECTORY/php-cs/` | `/tmp/php-cs-fixer/config/` | Configuration file (`.php-cs-fixer.php`) |
| `PROJECT_DATA_DIRECTORY/php-cs/` | `/tmp/php-cs-fixer/cache/` | Fixer cache (speeds up repeated runs) |

### Configuration

On first run, if no `.php-cs-fixer.php` is found in the config volume, the entrypoint seeds it with a default configuration:

```php
$finder = PhpCsFixer\Finder::create()
    ->in('/var/www/html');

return (new PhpCsFixer\Config())
    ->setRules([
        '@PSR12' => true,
        '@PHP80Migration' => true,
    ])
    ->setFinder($finder)
    ->setCacheFile('/tmp/php-cs-fixer/cache/.php-cs-fixer.cache');
```

To customise rules, edit the seeded file at `PROJECT_CONFIG_DIRECTORY/php-cs/.php-cs-fixer.php`. The file persists across container restarts because it lives in a mounted volume.

---

## Usage

All commands are executed inside the running container via `docker compose exec`.

```bash
docker compose exec php-cs make <target>
```

### Available targets

| Target | Description |
|---|---|
| `fix` | Fix all PHP files in the project |
| `check` | Dry-run — show what would be changed without modifying files |
| `dir-fix DIR=<path>` | Fix a specific directory relative to the project root |
| `dir-check DIR=<path>` | Dry-run check for a specific directory |
| `git-fix` | Fix only PHP files changed locally (uncommitted, staged, and vs base branch) |
| `git-check` | Dry-run check of the same git-scoped file sets |
| `git-action-check` | Check with `--format=checkstyle` output (for GitHub Actions annotations) |
| `bitbucket-check` | Dry-run check with plain diff output (for Bitbucket Pipelines) |

### Examples

**Fix all files in the project:**
```bash
docker compose exec php-cs make fix
```

**Check without modifying (review mode):**
```bash
docker compose exec php-cs make check
```

**Fix a specific module or directory:**
```bash
docker compose exec php-cs make dir-fix DIR=src/MyModule
docker compose exec php-cs make dir-check DIR=src/MyModule
```

**Fix only your current changes (git-aware):**
```bash
# Fixes uncommitted changes, staged files, and files changed vs origin/main
docker compose exec php-cs make git-fix

# Same scope but dry-run only
docker compose exec php-cs make git-check
```

**Override the base branch for branch-diff analysis:**
```bash
docker compose exec php-cs make git-fix BASE_BRANCH=origin/develop
```

**CI/CD — GitHub Actions:**
```bash
docker compose exec php-cs make git-action-check
```

**CI/CD — Bitbucket Pipelines:**
```bash
docker compose exec php-cs make bitbucket-check
```

---

## Changing the PHP CS Fixer version

Update `PHP_CS_VERSION` in `.env`, then rebuild the service:

```bash
docker compose build php-cs
```

Available tags: https://github.com/PHP-CS-Fixer/shim/pkgs/container/shim

---

## Customising rules

Edit `PROJECT_CONFIG_DIRECTORY/php-cs/.php-cs-fixer.php` on the host. The file is mounted into the container and read on every run, so changes take effect immediately without rebuilding.

Example — switching to the Symfony ruleset:

```php
return (new PhpCsFixer\Config())
    ->setRules([
        '@Symfony' => true,
        'array_syntax' => ['syntax' => 'short'],
    ])
    ->setFinder($finder)
    ->setCacheFile('/tmp/php-cs-fixer/cache/.php-cs-fixer.cache');
```

Full list of available rules: https://cs.symfony.com/doc/rules/index.html
