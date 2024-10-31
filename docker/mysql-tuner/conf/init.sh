#!/bin/bash

database_user="$1"
database_password="$1"

sed -i "s/{DATABASE_USER}/$database_user/g" ~/.my.cnf
sed -i "s/{DATABASE_PASSWORD}/$database_password/g" ~/.my.cnf
