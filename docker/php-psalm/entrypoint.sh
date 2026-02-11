#!/bin/sh

# Copy psalm.xml to mounted volume if it doesn't exist yet
if [ ! -f /tmp/analysis/psalm/psalm.xml ]; then
    cp /opt/psalm/psalm.xml /tmp/analysis/psalm/psalm.xml
    echo "Generated psalm.xml in /tmp/analysis/psalm/"
fi

exec psalm --config=/tmp/analysis/psalm/psalm.xml "$@"
