# PebbleOS (hosted) for the iPod nano 7G

This is a NanoApps host for the real PebbleOS (Apache-2.0, Core Devices),
not a recreation of the UI. PebbleOS is fetched at build time and is not
vendored in this folder.

The 240x432 screen centres a 200x228 Pebble (Emery) panel. Buttons:

| Pebble | nano 7G |
| --- | --- |
| Back | Home (the round button under the screen) |
| Select | Play/Pause |
| Up | Volume Up |
| Down | Volume Down |

Sleep/Wake is not used, so it can still sleep the iPod.

The app must fit and run under both memory levels the launcher sees: about
3.9 MB free with a 2 MB largest block, and about 6 MB / 4 MB. If the
largest block is too small, it shows a clean NOT ENOUGH MEMORY screen and
does not start the kernel.

This is not ready for the iPod until a device-like host run has passed
(no host libc unwinder, the device relocatable `.hbapp` layout, Cortex-A5
VFPv4-D16). The shim and memory screen can be built now; the hosted kernel
is the next step (a single-thread fiber port of the PebbleOS posix arch,
plus a framebuffer and button HAL).

## Build and install (friend-pod, Disk Mode)

```
./start sdk build pebbleos
./start nabase add "PebbleOS"
```

Or `./start pebbleos build`, which fetches the pinned PebbleOS tree and
then runs the same SDK build. On a NanoApps-base iPod, only use
`./start nabase add`, not `./start apps add`.
