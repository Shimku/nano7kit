# PocketJS on the iPod nano 7G (nano7g target)

This folder holds nano7kit's **nano7g** target for
[PocketJS](https://github.com/pocket-nexus/pocketjs) (MIT, pocket-nexus /
Yifeng "Evan" Wang). PocketJS is not vendored: `./start pocketjs build`
fetches it at the commit pinned in `apps.json` (and `host/const.py`) and
applies `nano7g.patch`.

| File | What it is |
|---|---|
| `nano7g.patch` | The upstreamable target, as one patch against the pinned commit: `hosts/nano7g` (240x432 touch profile), `programs/nano7g` (Rust crate, host preview, NanoApps shim and Makefile), `tools/build/nano7g/build.ts`, `apps/nano7g-hero`, `apps/nano7g-tilt`, `apps/nano7g-samples` (nano7g manifests for five upstream samples), `Ui::press_target` in MicroTS, registrations and test rows. |
| `apps.json` | The pinned commit and the apps this tree builds and stages. |
| `scripts/*.script` | Scripted touch sessions for the host preview and the dry run. |
| `emu.py` | Unicorn dry run of a staged `.hbapp` (Cortex-A8, Thumb-2). |
| `asset_lookalikes.json` | Per staged file (SHA-256): words in baked fonts that equal an old fixed address, proven to be asset bytes at build time. |

## How it runs

MicroTS compiles the app's TypeScript model and TSX view into Rust ahead of
time, so those apps run with no JavaScript VM (JS QuickJS below is the
exception). The Rust crate links the
generated app with the PocketJS retained core and software rasterizer as a
`thumbv7a-none-eabi` static library (soft float, no MOVW/MOVT). The NanoApps
shim (`programs/nano7g/nanoapps/pocketnano.c`) is a `RAW_SURFACE` app:

- the core renders straight into the OS framebuffer (240x432 XRGB8888);
- one touch point per heartbeat is hit tested; the tapped control (or its
  nearest focusable parent) is pressed on touch down and stays highlighted
  until the finger lifts; volume up and down arrive as Up and Down;
- apps made for buttons get an on-screen pad, apps made for another screen
  size are scaled to fit (turned sideways when wider than the panel), and
  apps that use motion get the accelerometer;
- one library can hold several apps behind a menu; an app's menu key frees
  it and returns to the menu;
- the Rust core runs on a private 48 KB stack (the file
  `/Apps/Data/PocketJS/no_private_stack.txt` on the iPod turns that off);
- each app's Rust heap is one OS heap block sized from its measured ARM
  peak (0.3 to 1.4 MB with the private stack), taken only if the OS keeps
  1 MiB in one block and 2 MiB in all, registered as the app's aux block so
  the resident frees it when the app quits or another app opens; baked
  fonts are read from the image, not copied;
- a failure stops the app with its name and reason on screen, and logs it;
- files only under `/Apps/Data/PocketJS`; no audio, no network.

JS Hero is about 0.95 MB and JS Samples about 2.1 MB (code plus baked
fonts), over the 576 KB slot A staging, so `apps add` puts them on slot B
like the large ports. See [docs/POCKETJS.md](../docs/POCKETJS.md).

**JS QuickJS** runs JavaScript `.pocket` packages on QuickJS
(the canonical C guest). The packages are separate read-only files in
`/Apps/Data/PocketJS`, copied by `./start pocketjs packages`; QuickJS is
fetched at a pinned commit, not vendored. It opens on a PocketJS home
screen with Notes, Talk, Motion Lab (yui540), Cafe, Physics, Tilt, Type,
Fonts and more.

## Commands

```sh
./start pocketjs apps                 # list the nano7g apps and their staged size
./start apps add 'JS Hero'            # Disk Mode, one extra per reboot (prebuilt, nothing to install)
./start apps add 'JS Samples'         # seven PocketJS samples behind a menu
./start apps add 'JS QuickJS'         # the PocketJS home screen and its apps
./start pocketjs packages             # copy the .pocket, .qjsbc and font files to /Apps/Data/PocketJS (Disk Mode)
./start pocketjs log                  # the app logs from /Apps/Data/PocketJS/logs
./start pocketjs emu hero             # Unicorn dry run of the staged app (needs python unicorn)
./start pocketjs build hero           # rebuild from the pinned commit (needs bun, rustup, arm-none-eabi-gcc)
./start pocketjs build samples
./start pocketjs build quickjs        # also fetches QuickJS and dlmalloc at pinned hashes
./start pocketjs emu quickjs --os-blocks 4.0,1.5,1.0,0.5
```

Rebuild toolchain: Bun, Rustup with `nightly-2026-07-02` plus `rust-src`,
`1.98.1` plus `wasm32-unknown-unknown`, Arm GNU `arm-none-eabi-gcc`, and
`pyelftools`. The staged `.hbapp` in `untether/artifacts/extra` does not need
any of them.

## Credits

- [PocketJS](https://github.com/pocket-nexus/pocketjs) (MIT): runtime, MicroTS compiler, core, rasterizer, and the Hero, GBA Hero, iPod nano Hero, Solid AOT Lab and Vue SFC Lab samples.
- [QuickJS](https://bellard.org/quickjs/) (MIT, Fabrice Bellard and Charlie Gordon), via [pocket-nexus/quickjs-rs](https://github.com/pocket-nexus/quickjs-rs); dlmalloc (public domain, Doug Lea); newlib; Inter (SIL OFL 1.1, Rasmus Andersson) in fonts/intl.pjfa.
- [NanoApps](https://github.com/nfzerox/NanoApps) (MIT): SDK, raw surface runtime, resident and loader.
- [nano7-untethered](https://github.com/olievans123/nano7-untethered): patched firmware so apps load at boot.
- [ipod_sun_untethered](https://github.com/nfzerox/ipod_sun_untethered) (GPL-3.0): firmware builder.
- [wInd3x](https://github.com/freemyipod/wInd3x) (GPL-2.0): DFU and recovery.
