#!/bin/sh

# Copy psalm.xml to mounted volume if it doesn't exist yet
if [ ! -f /tmp/psalm/config/psalm.xml ]; then
    cp /opt/psalm/psalm.xml /tmp/psalm/config/psalm.xml
    echo "[init] Generated psalm.xml in /tmp/psalm/config/"
fi

exec psalm --config=/tmp/psalm/config/psalm.xml "$@"
