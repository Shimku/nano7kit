#!/bin/bash
# Double-click in Finder to run ./install in Terminal.
cd "$(dirname "$0")" || exit 1
./install "$@"
rc=$?
echo
read -r -p "Press Enter to close this window. " _
exit $rc
