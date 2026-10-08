"""One-time narrowing of an existing database's accounts to the v2 privilege model."""

# Runs inside the database container. Databases created before setup v2 gave the
# application user ALL PRIVILEGES ON *.* WITH GRANT OPTION and used the application
# password for root. Afterwards root uses DATABASE_ROOT_PASSWORD (MYSQL_ROOT_PASSWORD
# in the container) and the application user has all privileges on its database only.
RESTRICT = r'''
set -eu
client=$(command -v mariadb || command -v mysql)
run() {  # run USER PASSWORD SQL — socket first (root@localhost), then TCP ('%' accounts)
    MYSQL_PWD="$2" "$client" --protocol=socket --user="$1" --batch --skip-column-names mysql -e "$3" 2>/dev/null ||
    MYSQL_PWD="$2" "$client" --protocol=TCP --host=127.0.0.1 --user="$1" --batch --skip-column-names mysql -e "$3"
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
database=$(printf "%s" "$MYSQL_DATABASE" | sed 's/`/``/g')
root_password=$(printf "%s" "$MYSQL_ROOT_PASSWORD" | sed "s/'/''/g")
root_hosts=$(run "$login_user" "$login_password" "SELECT Host FROM mysql.user WHERE User = 'root'")
user_hosts=$(run "$login_user" "$login_password" "SELECT Host FROM mysql.user WHERE User = '$user'")
# 1. Whoever could log in gives root its own password; 2. root then narrows the application user.
statements="SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';"
for host in $root_hosts; do statements="$statements ALTER USER 'root'@'$host' IDENTIFIED BY '$root_password';"; done
run "$login_user" "$login_password" "$statements"
# Revoking ALL at the global level also drops database-level grants, so grant afterwards.
statements="SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';"
for host in $user_hosts; do
    statements="$statements REVOKE ALL PRIVILEGES ON *.* FROM '$user'@'$host'; REVOKE GRANT OPTION ON *.* FROM '$user'@'$host';"
    statements="$statements GRANT ALL PRIVILEGES ON \`$database\`.* TO '$user'@'$host';"
done
run root "$MYSQL_ROOT_PASSWORD" "$statements"
echo "Root uses DATABASE_ROOT_PASSWORD; $MYSQL_USER has privileges on $MYSQL_DATABASE only (changed as $login_user)."
'''


def restrict_grants(project):
    running = project.capture(['ps', '--status', 'running', '--services']).split()
    if 'database' not in running:
        raise ValueError('Start the database first (make up or make db-prepare)')
    project.run(['exec', '-T', 'database', 'sh', '-c', RESTRICT])
