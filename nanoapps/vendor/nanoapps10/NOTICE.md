# NanoApps 1.0 base (vendored)

This tree vendors the MIT-licensed NanoApps 1.0 resident and related pieces from
https://github.com/nfzerox/NanoApps (commit 00d9fb3 / build-00d9fb3, "NanoApps 1.0").

Copyright (c) 2026 nfzerox (MIT). See LICENSE.MIT-nfzerox.
Bluetooth and dock fixes: Copyright (c) 2026 Merquice, Raphmatt (MIT). See LICENSE.compat.

nano7kit builds on this foundation as a superset: same .nanoapp apps keep working.

From NanoApps 1.0: clean refuse when the nothrow allocator returns NULL (the
image is not loaded and the app screen is not opened).

Chunked reads and the largest-block check are not in 1.0. They are in nano7kit's
patched SDK (`upstream/nano7-untethered.patch` on `hb_reloc_file.c` / `hb_fs.c`):
files over 512 KB on volume 0 are read in 256 KB pieces, and load refuses if
`hb_os_heap_largest()` is below the image plus alignment plus 256 KB.

`/Apps/Data` with `/Data` fallback is also nano7kit (`heavy_launch.c`), not 1.0.

The files in upstream-resident/ are NanoApps' launcher source as of that commit,
kept for reference. Only comment punctuation differs (em dashes written as en dashes).
