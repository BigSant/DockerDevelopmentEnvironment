"""Where setup writes a key into an env file: under its section, sections in a fixed order."""
import re

# Section title (an env file comment) and its keys, in the order they are written.
SECTIONS = (
    ('Project', ('PROJECT_NAME', 'PROJECT_DISPLAY_NAME', 'PROFILE', 'SETUP_MIN_VERSION', 'CI_PROVIDER')),
    ('Versions', ('PHP_VERSION', 'DATABASE_ENGINE', 'MYSQL_VERSION', 'MARIADB_VERSION', 'XDEBUG_VERSION',
                  'COMPOSER_VERSION')),
    ('PrestaShop', ('PS_VERSION', 'PS_RELEASE', 'PS_RELEASE_PATHS', 'PS_ADMIN_DIR', 'PS_LOCALE',
                    'PS_DATABASE_PREFIX', 'PS_SSL', 'PS_DEBUG_MODE', 'PS_DEBUG_IPS')),
    ('Services', ('SERVICES', 'CRON_SCHEDULE')),
    ('Address', ('DOMAIN', 'SQL_DOMAIN', 'LOCALHOST_PORT', 'LOCALHOST_PORT_SSL', 'PUBLISH_ADDRESS')),
    ('Access', ('ALLOWED_IPS', 'SITE_ALLOWED_IPS', 'PMA_ALLOWED_IPS', 'MAILPIT_ALLOWED_IPS', 'CRON_ALLOWED_IPS',
                'DBMONITOR_ALLOWED_IPS', 'BACKUP_ALLOWED_IPS')),
    ('Database', ('DATABASE_NAME', 'DATABASE_USER', 'DATABASE_PASSWORD', 'DATABASE_ROOT_PASSWORD',
                  'DATABASE_EXTRA_SCHEMAS')),
    ('PrestaShop keys', ('PS_COOKIE_KEY', 'PS_COOKIE_IV', 'PS_NEW_COOKIE_KEY', 'PS_SECRET', 'PS_RIJNDAEL_KEY',
                         'PS_RIJNDAEL_IV', 'PS_API_PRIVATE_KEY', 'PS_API_PUBLIC_KEY')),
    ('Database as code', ('DATABASE_AS_CODE', 'DATABASE_MIGRATIONS', 'SCHEMA_DIRECTORY', 'POST_IMPORT_SQL_DIRECTORY', 'FIXTURES_DIRECTORY')),
    ('Backup server', ('BACKUP_SSH', 'BACKUP_SERVER_PROJECT', 'BACKUP_SSH_KEY')),
    ('Runtime', ('CACHE_MODE', 'MAIL_MODE', 'HTTP_FORCE_HTTPS')),
    ('Tools', ('PROJECT_COMPOSE_FILES', 'PHPSTAN_BASELINE_FILE', 'PLAYWRIGHT_COMMAND')),
    ('Test copy', ('APP_SOURCE_DIRECTORY', 'DATA_DIRECTORY')),
)
SECTION = {key: index for index, (_, keys) in enumerate(SECTIONS) for key in keys}
ORDER = {key: position for _, keys in SECTIONS for position, key in enumerate(keys)}
KEY = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=')


def header(index):
    return f'# {SECTIONS[index][0]}'


def insert(contents, key, line):
    """contents with a new key line: after the keys of its section that come before it, the section
    created (header, blank line around it) before the first later section; unknown keys go last."""
    lines = contents.splitlines()
    if key not in SECTION:
        while lines and not lines[-1].strip():
            lines.pop()
        block = len(lines) - next((i for i, text in enumerate(reversed(lines)) if not text.strip()), len(lines))
        separate = block < len(lines) and lines[block] in {header(i) for i in range(len(SECTIONS))}
        return '\n'.join(lines + ([''] if separate else []) + [line]) + '\n'
    index = SECTION[key]
    headers = {i: lines.index(header(i)) for i in range(len(SECTIONS)) if header(i) in lines}
    if index not in headers:
        later = [position for i, position in headers.items() if i > index]
        at = min(later) if later else len(lines)
        block = [header(index), line]
        before, after = lines[:at], lines[at:]
        while before and not before[-1].strip():
            before.pop()
        return '\n'.join(before + ([''] if before else []) + block + ([''] if after else []) + after) + '\n'
    at = headers[index] + 1
    while at < len(lines) and lines[at].startswith('#') and not KEY.match(lines[at].lstrip('# ')):
        at += 1  # the section's own description stays right under its header
    position = at
    while position < len(lines) and lines[position].strip():  # a section ends at a blank line
        match = KEY.match(lines[position].lstrip('# '))
        if match and ORDER.get(match[1], -1) < ORDER[key]:
            at = position + 1
        position += 1
    return '\n'.join(lines[:at] + [line] + lines[at:]) + '\n'
