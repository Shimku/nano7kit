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

Files here:

- `pebbleos.c`: the NanoApps side (screen, buttons, memory check, the plain
  error screens, putting .data back on a relaunch)
- `host_hal.c`: time, the file-backed flash under `/Apps/Data/PebbleOS`, and
  starting the kernel
- `overlay/`, `apply_overlay.py`: the nano7_emery board and the patches
  applied to the fetched PebbleOS tree
- `pebbleos.ld`: the relocatable link for `mkrelocapp`
- `emu.py`: the Unicorn run used by `tests/test_pebbleos.py`

Details, test coverage and what is not known yet: `docs/PEBBLEOS.md`.
The emulator stills are in `screenshots/`. That is a host dry run, not a
pass on an iPod.

## Build and install (NanoApps base, Disk Mode)

```
./start pebbleos fetch
./start sdk build pebbleos
./start nabase add "PebbleOS"
```

`./start sdk build pebbleos` fetches the pinned tree if needed and always
links the real kernel. `nabase add` also copies the resource banks to
`/Apps/Data/PebbleOS/flash`. On a NanoApps-base iPod, only use
`./start nabase add`, not `./start apps add`.
