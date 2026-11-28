# PebbleOS on the iPod nano 7G

PebbleOS (https://github.com/coredevices/PebbleOS, Apache-2.0, Core Devices
LLC and contributors) runs as one NanoApps app. The kernel, the launcher and
the watchfaces are the real PebbleOS code, fetched at a pinned commit
(94e3d98) when you build. It is not vendored here and not a lookalike. The
nano 7G board files and the small patches nano7kit applies to that tree are in
`nanoapps/apps/pebbleos/overlay` and `apply_overlay.py`.

Status: it boots to the TicToc watchface, opens the launcher and goes back in
an emulator that loads the real `.hbapp` the way the launcher does. It has
not run on an iPod yet.

## Screens and buttons

The nano is 240x432. PebbleOS draws the Emery (Pebble Time 2) screen, 200x228,
at 1x in the middle with a dark bezel around it. 2x would not fit.

| Pebble | nano 7G |
| --- | --- |
| Back | Home |
| Select | Play/Pause |
| Up | Volume Up |
| Down | Volume Down |

Sleep/Wake is left to the iPod. From the watchface, Play/Pause opens the
launcher and Home goes back. Home also leaves the app in NanoApps, so on the
watchface it is the way out. Whether a short press of Home reaches PebbleOS
as Back before the iPod takes it has to be checked on the device.

Stills from the emulator, all 240x432, are in
`nanoapps/apps/pebbleos/screenshots/`:

- `splash-240x432.png`: the PebbleOS boot screen
- `watchface-tictoc-240x432.png`: TicToc, the default face
- `launcher-240x432.png`: the launcher after Play/Pause
- `low-memory-240x432.png`: the NOT ENOUGH MEMORY screen
- `no-resources-240x432.png`: shown when `/Apps/Data/PebbleOS` is missing
- `stopped-240x432.png`: shown when the kernel asks to reset

## Memory

The app has to run at both levels the launcher sees:

- tight: about 3.9 MB free, 2 MB largest block
- roomy: about 6 MB free, 4 MB largest block

The launcher puts the whole image in one block: 1.27 MB of span (0.8 MB of
code and data, the rest is .bss with the fiber stacks and one 64 KiB flash
bank). The `.hbapp` file is about 0.85 MB. After that the app takes one more
block, the app's aux block, which the launcher frees when you leave: the
384 KiB kernel heap plus up to three more 64 KiB flash banks if there is room
(576 KiB in all). If the largest block cannot hold 512 KiB, the app shows
NOT ENOUGH MEMORY and does not start the kernel. Pause music or close other
apps and open it again.

## Flash and saved state

PebbleOS expects a 4 MiB flash chip. Here it is a set of 64 KiB files under
`/Apps/Data/PebbleOS/flash/`, named by bank (`00.bin` to `3f.bin`):

- banks `00` to `0c`: system resources (fonts, icons, the boot logo), built
  from the fetched tree and copied by `./start nabase add PebbleOS`
- banks `20` to `3d`: the PebbleOS file system (settings, installed apps),
  made when PebbleOS first starts

A bank file that is missing reads as erased flash. Banks are kept in RAM and
written back once per frame at most, and a bank that is fully erased is
deleted rather than written. A boot with no saved state writes about 2.5 MB
while PebbleOS formats its file system; later boots write a few banks.

If `/Apps/Data/PebbleOS` is missing or a resource bank is missing, the app
shows NO RESOURCES and does not start the kernel. If the resources are there
but damaged, PebbleOS finds the bad checksum and asks to reset; the app then
shows STOPPED. Junk in the file system banks is fine: PebbleOS formats them
again and boots.

## How it is built

- The kernel runs on one NanoApps thread. Its threads are fibers switched
  with a small setjmp/longjmp pair in Thumb assembly on the kernel's own
  stacks. Nothing comes from a host libc or pthreads.
- Kernel and app are compiled with `-mcpu=cortex-a5 -mfpu=vfpv4-d16
  -mfloat-abi=soft`, newlib-nano, and linked with `pebbleos.ld` into a
  relocatable image that `mkrelocapp` packs as an HRL1 `.hbapp`. ARM unwind
  tables are discarded, so there is no host libc unwinder in the image.
  Soft float means no FPU registers are used at all.
- The launcher may open the app again in the image it already loaded. .bss
  is cleared on every launch; .data (6.5 KB) is copied aside when the app is
  loaded and put back on later launches, so the kernel always starts clean.
- Bluetooth and the phone app link are compiled out. The accelerometer is
  PebbleOS's simulated one (it reads flat), not the nano's.

## Tests

`tests/test_pebbleos.py` runs the built `.hbapp` in Unicorn
(`nanoapps/apps/pebbleos/emu.py`). The image is loaded and relocated from the
HRL1 file as the launcher does it, the OS heap is modelled as the real block
sizes, and the app reaches the OS only through the NanoApps SDK hooks.
Unicorn has no Cortex-A5 model, so it runs as a Cortex-A7; a disassembly
check makes sure nothing uses NEON or the registers the A5 does not have.

It checks:

- the image is HRL1, under 1.6 MB of span, and has the kernel linked in
- no unwinder tables or unwinder symbols, no NEON, no d16 to d31
- boot to the watchface at 3.9 MB / 2 MB and at 6 MB / 4 MB
- Play/Pause opens the launcher and Home goes back
- opening the app again in the same image boots again and does not leak
- first boot with no save, second boot from the saved state, and a corrupt
  save
- no flash files, a missing resource bank and damaged resources each give a
  clean screen
- the NOT ENOUGH MEMORY screen when the largest block is 256 KB

This is a host dry run. It says nothing about speed on the nano, the real
file system's write speed, or how Home behaves.

## Build and install

You need `arm-none-eabi-gcc` (14 or later, from `./start deps`), `cmake`,
Python 3 and git. `./start pebbleos deps` makes `build/pbl-venv` with the
Python tools the PebbleOS build uses.

```
./start pebbleos fetch
./start sdk build pebbleos
./start nabase add "PebbleOS"
```

`./start sdk build pebbleos` fetches and patches the pinned tree if needed,
builds the kernel, stages the resource banks in
`untether/artifacts/extra/Data/PebbleOS`, and links the `.hbapp`. It refuses
to finish if the kernel is not in the image. `./start nabase add` (iPod in
Disk Mode, NanoApps base) copies the app and the resource banks. It does not
touch the file system banks, so your watch settings survive a reinstall. Use
`nabase add`, not `./start apps add`, on the NanoApps base.

`./start pebbleos build` does the same as `./start sdk build pebbleos`, and
`./start pebbleos status` shows the pinned commit and what is built.

## Credits

PebbleOS is Apache-2.0. The fetched tree keeps its LICENSE and NOTICE files;
`nanoapps/apps/pebbleos/NOTICE` and `CREDITS.md` name it. Files in the
overlay that are new for the nano carry their own SPDX headers.
