# PhpStorm project setup

From a prepared project's Makefile directory, run:

```sh
make init
# Fill env/local.env with this machine's settings.
make ide-init
```

Open that directory in PhpStorm. For consolidated sources this is `<project>/app`,
with application code in `public/`. Reopen an already open project after the
initial module migration so PhpStorm reloads the project model.

The command prepares the project name, PHP language level from `PHP_VERSION`,
a project-local Docker Compose PHP interpreter, Git mappings for the environment
and application checkouts, and these Shell Script run configurations:

| Configuration | Command |
| --- | --- |
| Setup: Prepare IDE | `make ENV=local ide-init` |
| Setup: Start | `make ENV=local up` |
| Setup: Stop | `make ENV=local down` |
| Setup: Check | `make ENV=local doctor` |
| Setup: PHP shell | `make ENV=local shell` |
| Setup: Export schema | `make ENV=local schema-export` |
| Setup: Check schema | `make ENV=local schema-check` |

Schema actions are generated only when `SCHEMA_DIRECTORY` is set. `ENV=stage`
or `ENV=prod` selects that environment when initializing, including its private
env file and interpreter. One environment is selected as the IDE default at a time.

The shared startup task runs **Prepare IDE only**. PhpStorm must trust the project
before startup tasks execute. Docker, PHP and Shell Script support must be enabled.
Build missing images explicitly with `make build`; use **Start** before invoking
PHP through the interpreter, which executes in the running `php-fpm` container.
Initialization does not start containers or import a database.

## Docker connection and private machine settings

Docker connections are global PhpStorm settings. On Linux, `ide-init` reads
`$XDG_CONFIG_HOME/JetBrains/PhpStorm*/options/remote-servers.xml` (default
`~/.config`) and finds a connection matching the current Docker endpoint. It
does not write global settings. If none matches, add a connection named `Docker`
in PhpStorm, or specify an existing connection explicitly:

```sh
make ide-init IDE_DOCKER_SERVER='Docker'
# A nonstandard Linux IDE configuration location:
make ide-init IDE_CONFIG_DIRECTORY=/path/to/PhpStorm/config
```

Startup discovery uses the default config location. For an explicit connection
or custom location to remain selected automatically, supply these Make variables
through your local Make configuration (for example a locally included Makefile).

The native interpreter reads `.generated/phpstorm-compose.local.yaml`. This
mode-0600 file contains resolved machine paths and environment values, including
credentials; keep `.generated/` ignored. It is refreshed at initialization and
project startup. After changing env/Compose files during an open IDE session, run
**Prepare IDE** again, or `make ide-refresh` to refresh only the Compose file.
Successful Make runner commands also refresh an existing IDE bridge, including after env/Compose changes. Regular Make commands always use the original project/shared sources.

Existing unrelated IDE components and custom run files are preserved. Files
changed by initialization are backed up under `.generated/phpstorm-backups/`.
The default interpreter selection is local in `.idea/workspace.xml`.

## Sharing through Git

Commit only the project `.name`, `<name>.iml`, `modules.xml`, `php.xml`, `vcs.xml`,
`startup.xml` and managed `runConfigurations/setup_*.xml` files. Keep workspace,
shelf, data source credentials and other personal state ignored. The generator
does not edit `.gitignore`; select this whitelist in each adopting repository.

With these files shared, a trusted first project opening runs Prepare IDE and
creates the local Compose bridge/default interpreter selection. A new checkout
still needs the shared setup checkout, Docker CLI and valid private env values;
`ide-init` creates a missing private env file from its example, never real credentials.
For other IDE versions, verify the generated configurations after upgrading.
The XML format was checked against installed PhpStorm 2026.2 plugin classes.

See JetBrains documentation for [Docker Compose interpreters](https://www.jetbrains.com/help/phpstorm/configuring-remote-interpreters.html),
[shared run configurations](https://www.jetbrains.com/help/phpstorm/run-debug-configuration.html),
[startup tasks](https://youtrack.jetbrains.com/articles/SUPPORT-A-2129/How-to-execute-commands-scripts-on-projects-startup)
and [project trust](https://www.jetbrains.com/help/phpstorm/project-security.html).
