# Doctrine Migrations

## What is Doctrine Migrations?

[Doctrine Migrations](https://www.doctrine-project.org/projects/migrations.html) is a database schema versioning library for PHP. It allows you to manage incremental, reversible changes to your database schema as versioned PHP classes — keeping your schema in sync with your application code across all environments and team members.

Each migration is a PHP class with an `up()` method (apply the change) and a `down()` method (revert the change). Doctrine tracks which versions have been applied in a dedicated table (`doctrine_migration_versions`), so it always knows exactly what to run next.

### What it solves

- Propagating schema changes across dev, staging and production reliably
- Rolling back a broken deployment without manual SQL
- Reviewing schema history in version control alongside application code
- Avoiding "works on my machine" database drift between team members

---

## Service overview

The `php-doctrine-migrations` service runs the `doctrine-migrations` CLI inside a dedicated Docker container. It uses:

- **Base image**: `php:${PHP_VERSION}-cli` — same PHP version as the project
- **`doctrine/migrations`** installed globally via Composer at build time
- **`pdo_mysql`** extension for database connectivity

The container connects to the `database` service over the shared Docker network using the `DATABASE_*` environment variables from `.env`.

### Configuration

Both configuration files are baked into the image at `/opt/doctrine-migrations/` and used directly — no project-specific config files are needed:

- **`doctrine-migrations.php`** — migration behaviour (table name, paths, transaction settings)
- **`migrations-db.php`** — database connection, reads `DATABASE_*` environment variables at runtime

### Volumes

| Host path | Container path | Purpose |
|---|---|---|
| `PROJECT_CONFIG_DIRECTORY/doctrine-migrations/versions/` | `/tmp/doctrine-migrations/config/versions/` | Migration class files |
| `PROJECT_DATA_DIRECTORY/doctrine-migrations/` | `/tmp/doctrine-migrations/cache/` | Internal cache |

The `versions/` directory is the only thing that lives on the host — it contains the migration class files that must be committed to version control.

---

## Usage

All commands are executed inside the running container via `docker compose exec`.

```bash
docker compose exec php-doctrine-migrations make <target>
```

### Available targets

| Target | Description |
|---|---|
| `status` | Show applied and pending migration versions |
| `migrate` | Apply all pending migrations |
| `rollback` | Revert to the previous migration version |
| `diff` | Auto-generate a new migration from the current schema diff |
| `generate` | Create a blank migration class file |
| `execute VERSION=<version>` | Apply a specific migration version (`--up`) |
| `execute-down VERSION=<version>` | Revert a specific migration version (`--down`) |
| `list` | List all available `doctrine-migrations` commands |
| `latest` | Show the latest available migration version |
| `current` | Show the currently applied migration version |
| `up-to-date` | Check whether all migrations have been applied |
| `sync-metadata` | Synchronise the metadata storage table |

### Examples

**Check which migrations are pending:**
```bash
docker compose exec php-doctrine-migrations make status
```

**Apply all pending migrations:**
```bash
docker compose exec php-doctrine-migrations make migrate
```

**Roll back the last applied migration:**
```bash
docker compose exec php-doctrine-migrations make rollback
```

**Generate a migration from entity/schema changes:**
```bash
docker compose exec php-doctrine-migrations make diff
```

**Create a blank migration to write manually:**
```bash
docker compose exec php-doctrine-migrations make generate
```

**Apply a specific version:**
```bash
docker compose exec php-doctrine-migrations make execute VERSION="DoctrineMigrations\\Version20240101120000"
```

**Revert a specific version:**
```bash
docker compose exec php-doctrine-migrations make execute-down VERSION="DoctrineMigrations\\Version20240101120000"
```

**Confirm everything is up to date:**
```bash
docker compose exec php-doctrine-migrations make up-to-date
```

---

## Typical workflow

### First-time setup on a new environment

```bash
# 1. Start the service (database must be running)
docker compose up -d php-doctrine-migrations

# 2. Apply all existing migrations to bring the schema up to date
docker compose exec php-doctrine-migrations make migrate
```

### Adding a schema change

```bash
# 1. Generate a migration from your changes
docker compose exec php-doctrine-migrations make diff

# 2. Review the generated file in PROJECT_CONFIG_DIRECTORY/doctrine-migrations/versions/

# 3. Apply it
docker compose exec php-doctrine-migrations make migrate

# 4. Commit the new migration file to version control
```

### Deployment rollback

```bash
# Revert the last migration
docker compose exec php-doctrine-migrations make rollback

# Or revert to a specific known-good version
docker compose exec php-doctrine-migrations make execute-down VERSION="DoctrineMigrations\\Version20240101120000"
```

---

## Changing the Doctrine Migrations version

Update `DOCTRINE_MIGRATIONS_VERSION` in `.env`, then rebuild:

```bash
docker compose build php-doctrine-migrations
```

Available releases: https://github.com/doctrine/migrations/releases
