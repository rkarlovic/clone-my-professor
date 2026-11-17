# Dev only: a local Moodle to test the LTI 1.3 launch and Web Services (build step 4).
# Moodle HQ's own PHP/Apache image (pinned by digest) + the official Moodle 5.0.12 release
# (checked against its SHA-256). Moodle 5.1+ serves from public/, which needs another docroot.
FROM moodlehq/moodle-php-apache@sha256:3e910075c08dc73c8e18732f5e80c8fb9a60ae72eaf52b1ee198bb510e4fd9d6
ADD --checksum=sha256:84d75a5bd743c56c3b4546c19897b10a3d0c433a3a9e1ef3bfc8218644edd95c \
    https://github.com/moodle/moodle/archive/refs/tags/v5.0.12.tar.gz /tmp/moodle.tar.gz
RUN tar -xzf /tmp/moodle.tar.gz -C /var/www/html --strip-components=1 \
    && rm /tmp/moodle.tar.gz \
    && mkdir -p /var/www/moodledata \
    && chown -R www-data:www-data /var/www/html /var/www/moodledata
