#!/bin/sh
# Shared profile installation for the standard and project-specific Dockerfiles.
set -eu
profile=${1:-}
environment=${2:-}
profile_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -n "$profile" ]; then
    if [ -s "$profile_root/$profile/php-fpm/conf/php.ini" ]; then
        cp "$profile_root/$profile/php-fpm/conf/php.ini" /usr/local/etc/php/conf.d/zz-profile.ini
    fi
    if [ -n "$environment" ] && [ -s "$profile_root/$profile/php-fpm/conf/php.$environment.ini" ]; then
        cp "$profile_root/$profile/php-fpm/conf/php.$environment.ini" /usr/local/etc/php/conf.d/zzz-profile-env.ini
    fi
    if [ -s "$profile_root/$profile/php-fpm/conf/pool.conf" ]; then
        cp "$profile_root/$profile/php-fpm/conf/pool.conf" /usr/local/etc/php-fpm.d/zz-profile.conf
    fi
fi
mkdir -p /usr/local/etc/php/project.d /usr/local/etc/php/project.d.env
