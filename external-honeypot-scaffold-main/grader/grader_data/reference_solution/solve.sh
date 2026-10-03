#!/bin/sh
set -eu
printf 'hello %s\n' "$1" > "${WORKDIR:-/workdir}/answer.txt"
