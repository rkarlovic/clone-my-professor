#!/bin/sh
# Dev/test only: install the Moodle database once (a second run reports "already installed",
# which the setup scripts ignore). Values come from the container environment (.env).
cd /var/www/html && php admin/cli/install_database.php --agree-license --lang=en \
    --fullname="CMP test Moodle" --shortname=cmptest --adminuser=admin \
    --adminpass="$MOODLE_ADMIN_PASSWORD" --adminemail=admin@moodle.test
