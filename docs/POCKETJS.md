# PocketJS on the iPod nano 7G

[PocketJS](https://github.com/pocket-nexus/pocketjs) (MIT, Yifeng "Evan" Wang /
pocket-nexus) draws TypeScript components as native pixels, either with a
QuickJS guest plus the Rust core, or with MicroTS, which compiles the app to
Rust ahead of time. nano7kit adds a **nano7g** target (240x432 touch) and runs
PocketJS apps on the nano 7G as NanoApps. Target files: [pocketjs/README.md](../pocketjs/README.md).

PocketJS is fetched at a pinned commit and patched with `pocketjs/nano7g.patch`.
It is not vendored. The staged `.hbapp` and `.pocket` files are build outputs, so
a Mac needs no extra tools to install them. Nothing is flashed.

## The apps

| App | What it is |
|---|---|
| JS QuickJS | A PocketJS home screen with a clock, battery and two pages of icons: Notes (landscape, QWERTY keyboard, saves to the iPod), Talk, Motion Lab, Cafe, Physics, Tilt, Gestures, Type, Fonts, JS Hero, Vue Lab and Memo. Apps are `.pocket` packages in `/Apps/Data/PocketJS`. |
| JS Hero | The PocketJS Hero scene, compiled ahead of time. |
| JS Samples | GBA Hero, iPod nano Hero, Hero, Solid AOT Lab, Vue SFC Lab and Tilt behind a menu. |
| Free RAM | A small NanoApp that shows the OS heap: largest block, free in all, whether JS QuickJS will open in full, in low-memory mode or not at all, and what Pause or Stop music freed. |

Motion Lab is yui540's four motion studies from upstream `apps/motions`. The
`(yui540)` credit stays in the bottom right of every page.

```sh
./install                          # pick JS QuickJS, JS Hero or JS Samples in the menu
./start nabase add 'JS QuickJS'    # or by hand, in Disk Mode, then:
./start nabase packages            # copy the .pocket files to /Apps/Data/PocketJS
./start pocketjs apps              # list the apps and their sizes
```

On the nano7-untethered layout the same steps are `./start apps add` and
`./start pocketjs packages`.

## Write your own

A PocketJS app is a folder with a `pocket.app.json` manifest and TSX files, written
like a small web app (Solid or Vue components, Tailwind-style classes). The upstream
docs are at [pocketjs.pocket.nexus](https://pocketjs.pocket.nexus/docs/cli/).
`apps/nano7g-physics` in the patched checkout is a small example: six balls in a box,
two buttons and a hit counter. Its manifest asks for a fixed 240x432 screen:

```json
"viewport": { "fixed": { "logical": [240, 432], "presentation": "native" } }
```

A counter, cut down from `apps/nano7g-hero/app.tsx`:

```tsx
import { createSignal } from "solid-js";
import { Text, View } from "@pocketjs/framework/solid/components";

export default function App() {
  const [count, setCount] = createSignal(0);
  return (
    <View class="w-full h-full flex-col items-center justify-center bg-slate-50">
      <Text class="text-4xl font-bold text-slate-950">{count()}</Text>
      <View class="w-[140] h-[56] items-center justify-center rounded-xl bg-blue-600"
            focusable onPress={() => setCount(count() + 1)}>
        <Text class="text-lg font-bold text-white">Tap +1</Text>
      </View>
    </View>
  );
}
```

1. `./start pocketjs build hero` fetches PocketJS at `6ca9f914` into
   `build/pocketjs-6ca9f914f6/` and applies `pocketjs/nano7g.patch`. It needs bun,
   rustup and `arm-none-eabi-gcc`.
2. Put your app in that checkout next to the others, with its `pocket.app.json`.
3. Register it:
   - **compiled ahead of time** (MicroTS, no JavaScript engine on the iPod): add it to
     `samples` in `pocketjs/apps.json` and to the JS Samples list there, and add a
     `Spec` (screen size, input, memory) in `programs/nano7g/src/samples/mod.rs`;
   - **run on QuickJS** (inside JS QuickJS): add it to `packages` in
     `pocketjs/apps.json`, to the app list in `programs/nano7g/src/js.rs`, and give
     it an icon in `apps/nano7g-home`.
4. Measure its memory without the iPod: `./start pocketjs emu samples` (or
   `quickjs`). Set the heap from the measured peak.
5. Build and copy: `./start pocketjs build samples` (or `quickjs`), then in Disk
   Mode `./start nabase add 'JS Samples'`, or `./start nabase add 'JS QuickJS'` and
   `./start nabase packages` for QuickJS apps.
6. Keep your changes: regenerate `pocketjs/nano7g.patch` from the checkout
   (`git diff` against `6ca9f914`, new files included). A checkout that no longer
   matches the patch is refused on the next build.

## Memory

A fresh boot with the tested Home set has about 7.0 MB free and a 4.0 MB
largest block. Each app takes one OS heap
block sized from its measured peak, gives it back when it returns to the
menu, and reads baked fonts from its image instead of copying them.

**Low-memory mode.** Music playback and Bluetooth headphones keep a
buffer inside the largest block that no known SDK call releases. When the
largest block is under what Home needs, JS QuickJS takes what is there
(leaving the OS 512 KB largest and 1.5 MB free) and Home shows
LOW MEMORY: MUSIC OR BLUETOOTH. Big apps such as Motion Lab may then stop
with OUT OF MEMORY. For the full size: turn Bluetooth off and stop music
in the Music app, or restart without starting music. Free RAM tells you
which mode you will get.

**If an app restarts the iPod.** The Rust core runs on a private 48 KB
stack. To try the OS stack instead, in Disk Mode:

```sh
mkdir -p /Volumes/IPOD/Apps/Data/PocketJS
touch /Volumes/IPOD/Apps/Data/PocketJS/no_private_stack.txt   # off
rm /Volumes/IPOD/Apps/Data/PocketJS/no_private_stack.txt      # back on
```

A failure shows a red screen with the app name and the reason, and is
written to the log.

## Logs

Each app writes `/Apps/Data/PocketJS/logs/<app>.txt` every 30 s, after a save
or a failure, and when you go back to the menu. To read it: put the nano in
Disk Mode ([BUTTONS.md](BUTTONS.md)) and run `sudo -E ./start pocketjs log`.
`./start pocketjs log FILE` reads a copied log.

## Building from source

```sh
./start pocketjs build hero        # needs bun, rustup, arm-none-eabi-gcc, pyelftools
./start pocketjs build samples
./start pocketjs build quickjs     # also fetches QuickJS and dlmalloc at pinned hashes
./start pocketjs emu hero          # Unicorn dry run of the staged app (host only)
```

## How the 7G maps the PocketJS platform layer

| Layer | 2G (Rockbox, `programs/ipodnano`) | 7G (`programs/nano7g`) |
|---|---|---|
| Drawing | RGB565 176x132 buffer | software rasterizer into the OS framebuffer, XRGB8888 240x432 |
| Input | click wheel as `input.buttons` | touch (two fingers), an on-screen pad for `input.buttons` apps, volume keys as Up/Down, accelerometer as `MotionState` |
| Display | 176x132 | 240x432; other sizes scaled to fit, turned for landscape apps |
| Timers | 30 Hz loop | one Runtime tick per 1/30 s, at most one per NanoApps heartbeat |
| Files | none | `.pocket` packages read-only; app data under `/Apps/Data/PocketJS` |
| Memory | audio buffer as heap | one OS heap block per app, freed on quit; top 48 KB is the private stack |
| Audio | stops while running | none |

The upstream `--target ipodnano` is the nano 2G Rockbox build (176x132,
click wheel). It is not used here.

`./start pocketjs hello` builds **JS Hello**, an older first-party
`pocket_hal.h` prototype. JS Hello is not the PocketJS runtime.

## What this toolkit will not do

- Vendor PocketJS sources
- Flash anything, combine PocketJS with the firmware, or replace the resident
- Write NAND: `apps add` is a Disk Mode copy to `/Apps`

## Credits

PocketJS is by Yifeng "Evan" Wang / [pocket-nexus](https://github.com/pocket-nexus/pocketjs),
MIT, pinned at `6ca9f914`. QuickJS is by Fabrice Bellard and Charlie Gordon (MIT).
Motion Lab is yui540's motion studies from upstream `apps/motions`. Inter is by
Rasmus Andersson (OFL-1.1). nano7kit's part is the nano7g target in
`pocketjs/nano7g.patch`.
