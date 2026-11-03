# NanoApps 1.0 base (vendored)

This tree vendors the MIT-licensed NanoApps 1.0 resident and related pieces from
https://github.com/nfzerox/NanoApps (commit 00d9fb3 / build-00d9fb3, "NanoApps 1.0").

Copyright (c) 2026 nfzerox (MIT). See LICENSE.MIT-nfzerox.
Bluetooth and dock fixes: Copyright (c) 2026 Merquice, Raphmatt (MIT). See LICENSE.compat.

nano7kit builds on this foundation as a superset: same .nanoapp apps keep working,
and heavy apps (JS QuickJS, PvZ, Temple Run, Fruit Ninja, SM64/MK64) get a reliable
loader (chunked reads, largest-block check, clean refuse, GL teardown, /Apps/Data
with /Data compat).

The files in upstream-resident/ are NanoApps' launcher source as of that commit,
kept for reference. Only comment punctuation differs (em dashes written as en dashes).
