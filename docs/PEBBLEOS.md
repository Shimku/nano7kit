# PebbleOS on the iPod nano 7G

PebbleOS (https://github.com/coredevices/PebbleOS, Apache-2.0) is hosted as
one NanoApps app. The kernel and UI come from that tree, fetched at a pinned
commit at build time, not vendored, and not rewritten as a lookalike.

## Screen and buttons

The nano is 240x432. The Emery Pebble UI is 200x228, drawn at 1x in the
centre of the panel (2x would not fit). Around it is a dark bezel.

| Pebble | nano 7G |
| --- | --- |
| Back | Home |
| Select | Play/Pause |
| Up | Volume Up |
| Down | Volume Down |

Sleep/Wake is left to the iPod. Touch on the Pebble panel will be wired
once the hosted kernel is linked.

## Memory

The app has to run under both levels:

- tight: about 3.9 MB free, 2 MB largest block
- roomy: about 6 MB free, 4 MB largest block

If the largest block cannot hold the working set, the app shows
NOT ENOUGH MEMORY and does not start the kernel. Pause music or close
other apps and open it again.

The NanoApps loader puts the whole `.hbapp` into one allocation, so the
image itself has to stay well under the 2 MB largest block, with heap,
the 200x228 framebuffer and file-backed flash on top. Until the kernel
is linked, the shim is small and those floors are modest. They will be
measured again after the first hosted link.

## What is left before it is called ready

- A nano 7G hosted board in the fetched PebbleOS tree (fiber threads on
  one NanoApps thread: the Emscripten posix fiber backend is not used
  here). Framebuffer and button HAL, file-backed "flash" under
  `/Apps/Data/PebbleOS`.
- A device-like host test: compile and run with no host libc unwinder,
  the device relocatable `.hbapp` layout (`mkrelocapp`), and
  `-mcpu=cortex-a5 -mfpu=vfpv4-d16`. A pass in a desktop qemu that still
  uses the host libc is not enough.
- Measure the image and peak heap on both memory levels, and update the
  NOT ENOUGH MEMORY floor from those numbers.
- Samsa or a built-in watchface so the first screen is a real Pebble
  watchface.

## Commands

```
./start pebbleos status
./start pebbleos fetch
./start sdk build pebbleos
./start nabase add "PebbleOS"
```

Use `nabase add` on the NanoApps base. Do not use `./start apps add`.
