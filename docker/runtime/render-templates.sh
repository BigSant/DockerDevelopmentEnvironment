#!/bin/sh
# Copy project drop-in directories into writable effective directories, rendering
# *.template files with the container environment.
#
# usage: render-templates.sh SOURCE DESTINATION [SOURCE DESTINATION ...]
#
# Only braced ${NAME} is substituted, so $uri, $host or PHP variables are untouched.
# An unset NAME is an error and the container does not start. Plain files are copied
# as they are. Top-level directories named after an environment (local, test, stage,
# prod) belong to their own mount and are skipped when rendering the common directory.
set -eu

render_file() {
    awk -v file="$1" '
    {
        line = $0; out = ""
        while (match(line, /\$\{[A-Za-z_][A-Za-z0-9_]*\}/)) {
            name = substr(line, RSTART + 2, RLENGTH - 3)
            if (!(name in ENVIRON)) {
                print "Unknown template variable ${" name "} in " file > "/dev/stderr"
                exit 2
            }
            out = out substr(line, 1, RSTART - 1) ENVIRON[name]
            line = substr(line, RSTART + RLENGTH)
        }
        print out line
    }' "$1"
}

render_directory() {
    source=$1
    destination=$2
    mkdir -p "$destination"
    find "$destination" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    [ -d "$source" ] || return 0
    (cd "$source" && find . -type f ! -path './local/*' ! -path './test/*' ! -path './stage/*' ! -path './prod/*') |
    while IFS= read -r relative; do
        relative=${relative#./}
        mkdir -p "$destination/$(dirname "$relative")"
        case "$relative" in
            *.template)
                target="$destination/${relative%.template}"
                render_file "$source/$relative" > "$target.tmp"
                mv "$target.tmp" "$target"
                ;;
            *)
                cp "$source/$relative" "$destination/$relative"
                ;;
        esac
    done
}

while [ "$#" -ge 2 ]; do
    render_directory "$1" "$2"
    shift 2
done
