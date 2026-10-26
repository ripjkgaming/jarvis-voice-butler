#!/bin/sh
exec /usr/bin/python3 "$(dirname "$0")/native-host.py" "$@"
