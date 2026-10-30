#!/usr/bin/env bash
# Ephemeral Ubuntu 24.04 CI runners only; never call this from Render builds.
set -euo pipefail
. /etc/os-release
test "$ID:$VERSION_ID" = ubuntu:24.04
test "$(id -u)" = 0
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg libssl3t64 openssl
install -d /usr/share/postgresql-common/pgdg
curl --fail --silent --show-error \
  https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  --output /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
fingerprint=$(gpg --show-keys --with-colons \
  /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc | awk -F: '$1 == "fpr" {print $10; exit}')
test "$fingerprint" = B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8
printf '%s\n' 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main' \
  > /etc/apt/sources.list.d/radar-pgdg.list
apt-get update
apt-get install -y --no-install-recommends libpq5
apt-cache policy libssl3t64 libpq5
