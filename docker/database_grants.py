"""One-time narrowing of an existing database's accounts to the v2 privilege model."""

# Runs inside the database container. Databases created before setup v2 gave the
# application user ALL PRIVILEGES ON *.* WITH GRANT OPTION and used the application
# password for root. Afterwards root uses DATABASE_ROOT_PASSWORD (MYSQL_ROOT_PASSWORD
# in the container) and the application user has all privileges on its database only, plus the
# extra schemas (DATABASE_EXTRA_SCHEMAS) passed as arguments, which are created when missing.
RESTRICT = r'''
set -eu
for schema in "$@"; do  # checked before any change; names also go into SQL and grant patterns
    case $schema in ''|*[!A-Za-z0-9_]*) echo "Invalid extra schema name: $schema" >&2; exit 64 ;; esac
done
client=$(command -v mariadb || command -v mysql)
run() {  # run USER PASSWORD SQL — socket first (root@localhost), then TCP ('%' accounts)
    # SQL goes on stdin: statements carry passwords, which a command line would show in ps.
    printf '%s\n' "$3" | MYSQL_PWD="$2" "$client" --protocol=socket --user="$1" --batch --skip-column-names mysql 2>/dev/null ||
    printf '%s\n' "$3" | MYSQL_PWD="$2" "$client" --protocol=TCP --host=127.0.0.1 --user="$1" --batch --skip-column-names mysql
}
# Pre-v2 databases may have any historical root password, but the application user then
# held CREATE USER and GRANT OPTION globally, so it can perform the change itself.
login_user=; login_password=
for account in "root:root_new" "root:app" "app:app"; do
    user_name=$MYSQL_USER; [ "${account%%:*}" = root ] && user_name=root
    password=$MYSQL_PASSWORD; [ "${account#*:}" = root_new ] && password=$MYSQL_ROOT_PASSWORD
    if run "$user_name" "$password" "SELECT COUNT(*) FROM mysql.user" >/dev/null 2>&1; then
        login_user=$user_name; login_password=$password; break
    fi
done
if [ -z "$login_user" ]; then
    echo 'Cannot connect as root or as a privileged application user; restore root access manually.' >&2
    exit 65
fi
user=$(printf "%s" "$MYSQL_USER" | sed "s/'/''/g")
# Escaped _ (as for the extra schemas): an unescaped _ in a database grant matches any character.
database=$(printf "%s" "$MYSQL_DATABASE" | sed 's/`/``/g; s/_/\\_/g')
root_password=$(printf "%s" "$MYSQL_ROOT_PASSWORD" | sed "s/'/''/g")
root_hosts=$(run "$login_user" "$login_password" "SELECT Host FROM mysql.user WHERE User = 'root'")
user_hosts=$(run "$login_user" "$login_password" "SELECT Host FROM mysql.user WHERE User = '$user'")
# 1. Whoever could log in gives root its own password; 2. root then narrows the application user.
statements="SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';"
for host in $root_hosts; do statements="$statements ALTER USER 'root'@'$host' IDENTIFIED BY '$root_password';"; done
run "$login_user" "$login_password" "$statements"
# Revoking ALL at the global level also drops database-level grants, so grant afterwards.
statements="SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';"
for schema in "$@"; do statements="$statements CREATE DATABASE IF NOT EXISTS \`$schema\`;"; done
for host in $user_hosts; do
    statements="$statements REVOKE ALL PRIVILEGES ON *.* FROM '$user'@'$host'; REVOKE GRANT OPTION ON *.* FROM '$user'@'$host';"
    statements="$statements GRANT ALL PRIVILEGES ON \`$database\`.* TO '$user'@'$host';"
    for schema in "$@"; do
        # Escaped _: an unescaped _ in a database grant matches any character.
        pattern=$(printf "%s" "$schema" | sed 's/_/\\_/g')
        statements="$statements GRANT ALL PRIVILEGES ON \`$pattern\`.* TO '$user'@'$host';"
    done
done
run root "$MYSQL_ROOT_PASSWORD" "$statements"
if [ $# -eq 0 ]; then
    echo "Root uses DATABASE_ROOT_PASSWORD; $MYSQL_USER has privileges on $MYSQL_DATABASE only (changed as $login_user)."
else
    echo "Root uses DATABASE_ROOT_PASSWORD; $MYSQL_USER has privileges on $MYSQL_DATABASE and $# extra schemas only (changed as $login_user)."
fi
'''


def restrict_grants(project):
    running = project.capture(['ps', '--status', 'running', '--services']).split()
    if 'database' not in running:
        raise ValueError('Start the database first (make up or make db-prepare)')
    from database_client import extra_schemas
    project.run(['exec', '-T', 'database', 'sh', '-c', RESTRICT, 'restrict-grants', *extra_schemas(project.settings)])


# The DB monitor account: query statistics only (performance_schema, sys, PROCESS), no project data.
MONITOR = r'''
set -eu
client=$(command -v mariadb || command -v mysql)
user=$(printf "%s" "$1" | sed "s/'/''/g")
# The password arrives on stdin and reaches the client on stdin, never on a command line.
IFS= read -r raw_password
password=$(printf "%s" "$raw_password" | sed "s/'/''/g")
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$client" --protocol=TCP --host=127.0.0.1 --user=root --batch mysql <<SQL
SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';
CREATE USER IF NOT EXISTS '$user'@'%' IDENTIFIED BY '$password';
ALTER USER '$user'@'%' IDENTIFIED BY '$password';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$user'@'%';
GRANT PROCESS ON *.* TO '$user'@'%';
GRANT SELECT ON performance_schema.* TO '$user'@'%';
GRANT SELECT, EXECUTE ON sys.* TO '$user'@'%';
SQL
echo "Monitor account $1 reads performance_schema, sys and the process list only."
'''


def monitor_account(project):
    """(user, password) of the DB monitor; the user defaults to <DATABASE_NAME>_monitor."""
    import re
    user = project.settings.get('DATABASE_MONITOR_USER') or f"{project.settings['DATABASE_NAME']}_monitor"
    password = project.settings.get('DATABASE_MONITOR_PASSWORD') or ''
    if not re.fullmatch(r'[A-Za-z0-9_]{1,32}', user):
        raise ValueError('DATABASE_MONITOR_USER must be 1-32 letters, digits or _')
    if len(password) < 16:
        raise ValueError(f'Set DATABASE_MONITOR_PASSWORD (16+ characters) in env/{project.environment}.env')
    if user in (project.settings.get('DATABASE_USER'), 'root'):
        raise ValueError('The monitor needs its own account, not the application user or root')
    return user, password


def create_monitor_user(project):
    user, password = monitor_account(project)
    running = project.capture(['ps', '--status', 'running', '--services']).split()
    if 'database' not in running:
        raise ValueError('Start the database first (make up or make db-prepare)')
    import subprocess
    command = project.command + ['exec', '-T', 'database', 'sh', '-c', MONITOR, 'monitor-user', user]
    subprocess.run(command, input=password + '\n', text=True, env=project.compose_env(['exec']), check=True)
