# nano7kit

Games and JavaScript apps for the iPod nano 7th generation, installed from a Mac.

![Super Mario 64 running on four iPod nano 7G units](docs/images/mario.png)

nano7kit 1.1 runs on top of [NanoApps 1.0](https://github.com/nfzerox/NanoApps) by nfzerox.
It keeps NanoApps' firmware and apps, and adds a launcher with room for big games, ports
you build from your own game files, and PocketJS, a way to write nano apps in TypeScript.

## What you get

- **A launcher for big apps.** A fork of the NanoApps launcher that checks free memory
  before loading an app, reads large files in pieces, and frees the app when you go
  back to Home. You can switch between games and apps without restarting the iPod.
- **Games from your own files:** Super Mario 64, Temple Run, Fruit Ninja and Plants vs.
  Zombies (PvZ Portable). No game data is in this repo.
- **PocketJS:** apps written in TSX with flexbox layout and signals, running on QuickJS
  on the nano, or compiled ahead of time.
- **The NanoApps apps:** Calculator, Notes, Chess, Paint, Tetris and about 40 more.
- **A Terminal installer** where you tick the apps you want and untick the ones you don't.
- **A porting guide** with four worked examples: [docs/PORTING.md](docs/PORTING.md).

Tested on one Mid-2015 nano (MKN52, 16 GB) with an Intel Mac on macOS 13.5. Apple silicon
is untested on hardware. Mac only.

## Quick start

On an iPod that already runs NanoApps 1.0, in Disk Mode:

```sh
git clone https://github.com/shimku/nano7kit.git && cd nano7kit && ./install
```

Starting from a stock iPod, or one on nano7-untethered? The full steps are at the
[bottom of this page](#full-setup-step-by-step).

![The Home screen with NanoApps apps, games and PocketJS](docs/images/home.png)

## Games: bring your own files

![Temple Run, Blocks, Plants vs. Zombies and Fruit Ninja on the nano](docs/images/games.png)

| Game | You supply | How it gets onto the nano |
|---|---|---|
| Super Mario 64 | your Super Mario 64 (USA) `.z64` | olievans123's port, fetched at a pinned commit and built on your Mac |
| Temple Run | your decrypted Temple Run 1.0 `.ipa` | olievans123's recompile, fetched and built, plus nano7kit's memory changes |
| Fruit Ninja | your decrypted Fruit Ninja 1.0 `.ipa` | recompiled from the iOS app by nano7kit (`games/fruitninja`) |
| PvZ Portable | your PC Plants vs. Zombies GOTY folder | the PvZ-Portable engine with a nano layer (`games/pvz_portable`) |

The installer checks your file's hash, builds the game on your Mac, and copies it in
Disk Mode. ROMs, `.ipa` files and anything built from them stay in ignored folders and
are never committed. Put your files in `mygames/` or drag them into Terminal when asked.
Details: [docs/GAMES.md](docs/GAMES.md).

## Build your own apps

As a platform, the nano 7G gives you one app at a time, a 240x432 touch screen, an
accelerometer, OpenGL ES 1.1, and about 4 MB for the largest app.

- **C with the NanoApps SDK.** NanoApps apps run unchanged with this launcher.
  ```sh
  ./start sdk new my_app --title "My App"   # from nanoapps/sdk/template
  ./start sdk build my_app
  ./start nabase add "My App"               # Disk Mode
  ```
- **TypeScript with PocketJS.** Write it like a small web app (below).
- **Port a game.** [docs/PORTING.md](docs/PORTING.md) shows how Temple Run, Fruit Ninja,
  Mario 64 and PvZ were done: pin the input, recompile or port, measure on the Mac,
  make it fit, check before writing.

## PocketJS

![PocketJS apps on the nano: a counter, the home screen, Motion Lab and Notes](docs/images/pocketjs.png)

[PocketJS](https://github.com/pocket-nexus/pocketjs) by pocket-nexus draws TSX components
(Solid or Vue) as native pixels. nano7kit adds a nano 7G target, so you can write an iPod
app with JSX, flexbox, Tailwind-style classes and signals, and run it on QuickJS on the
nano, or compile it ahead of time with MicroTS.

Why it is worth a try:

- You write a component, not a framebuffer loop. Layout, text, images, animation and
  touch (two fingers) are handled for you, and tilt arrives as `MotionState`.
- Apps are small `.pocket` packages in `/Apps/Data/PocketJS`. Adding one is a file copy.
- You can test on the Mac first: `./start pocketjs emu quickjs` runs the app without the
  iPod and reports its memory peak.

JS QuickJS is a home screen with Notes (QWERTY keyboard, saves to the iPod), Talk, Motion
Lab (yui540's motion studies), Cafe, Physics, Tilt, Gestures, Type and Fonts. Ideas for
your own: a to-do list, flashcards, a habit tracker, a clock face, a small game.

A counter, in the style of `apps/nano7g-hero`:

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

How to build, register and copy your own app: [docs/POCKETJS.md](docs/POCKETJS.md).
PocketJS is fetched at commit `6ca9f914` and patched with `pocketjs/nano7g.patch`.

## Known issues

- **Fruit Ninja:** the menu works, but swipes during a round are unreliable on the 1.1
  base. They worked on 1.0. A fix is in progress.
- More than 16 apps on Home is untested with this launcher.
- No game plays sound yet.
- Flappy Bird, Doodle Jump and Mario Kart 64 are in the menu but untested.

## History and fallback

nano7kit 1.0 ran on [nano7-untethered](https://github.com/olievans123/nano7-untethered) by
olievans123: his firmware, loader, Blocks, Game Boy, Probe and Mario ports. That route
needs a FAT32 disk, which erases the iPod, and Finder can no longer update an iPod that
runs it. 1.1 moves to NanoApps 1.0's firmware: no erase, music stays, and the firmware keeps
the USB command Finder uses for updates. The nano7-untethered route is still here as a fallback
([docs/FAT32.md](docs/FAT32.md)), with its recovery command `./start apps restore-tested`.

Compared with NanoApps on its own:

- NanoApps installs from Finder, iTunes or Apple Devices on a Mac or Windows, with no
  Terminal. nano7kit's extras need Terminal on a Mac.
- NanoApps' launcher keeps the last app loaded, so reopening it is quicker. nano7kit's
  frees it on Home so the next big app gets the whole memory block, and refuses an app
  unless the largest free block holds it plus 256 KB.
- NanoApps reports Flappy Bird and Doodle Jump working on a 2015 nano. Here they are untested.

## Credits

- [NanoApps](https://github.com/nfzerox/NanoApps) by nfzerox (MIT): firmware, app framework,
  SDK, apps and the launcher nano7kit's launcher is forked from. With the AirPods H2 fix by
  Merquice and the Lightning Dock fix by Raphmatt.
- [nano7-untethered](https://github.com/olievans123/nano7-untethered) and the Temple Run,
  Flappy Bird and Doodle Jump ports by olievans123. No licence is stated, so they are
  fetched at a pinned commit when you build, not included here.
- [PvZ-Portable](https://github.com/wszqkzqk/PvZ-Portable) by wszqkzqk (LGPL-3.0).
- [PocketJS](https://github.com/pocket-nexus/pocketjs) by pocket-nexus (MIT), pinned at
  `6ca9f914`. QuickJS by Fabrice Bellard and Charlie Gordon. Motion Lab by yui540.
- [Inter](https://rsms.me/inter/) by Rasmus Andersson (OFL-1.1).
- wInd3x by freemyipod, ipod_sun by CUB3D and nfzerox, LVGL, Font Awesome Free.

Full list: [CREDITS.md](CREDITS.md).

## Disclaimer

Unofficial. Use at your own risk: writing firmware can leave an iPod needing recovery.
Not affiliated with or endorsed by Apple, Nintendo, Imangi, Halfbrick, PopCap or any
game developer. No game files, ROMs or Apple firmware are included.

MIT, see [LICENSE](LICENSE). Some parts keep their own licence: [NOTICE](NOTICE).

## Full setup, step by step

**You need:** an iPod nano 7G on firmware 1.1.2 (Mid-2015 tested, 2012 untested), a Mac
(Intel tested; Apple silicon untested), and a Lightning cable. On the tested Mac, recovery
mode only showed up through a USB-A hub. Button steps: [docs/BUTTONS.md](docs/BUTTONS.md).

**1. Get nano7kit.**

```sh
git clone https://github.com/shimku/nano7kit.git && cd nano7kit
```

`./install` sets up its own build tools on an Intel Mac. Apple silicon needs Homebrew
first: [docs/MACOS.md](docs/MACOS.md).

**2. Put the NanoApps 1.0 firmware on the iPod.** This keeps your music and files. No
FAT32, no erase.

- *Stock iPod (Finder can update it):* run `./start nabase ipsw` to download and check
  `NanoApps-2015-Model.ipsw` (`--model 2012` for the 2012 nano). In Finder, select the iPod, hold Option, click **Check for
  Update** (not Restore) and pick that file. These are NanoApps' own
  [install steps](https://github.com/nfzerox/NanoApps#download-and-install-nanoapps).
- *iPod on nano7-untethered (Finder fails with error 1461):* write the same firmware in
  recovery mode. This is the path tested with nano7kit. Back up first, in Disk Mode:

  ```sh
  ./start backup      # full image, hash-checked; note the path and SHA-256 it prints
  ./start --i-accept-untested-flasher --i-accept-2015-refresh nabase flash --dry-run \
    --backup IMAGE --backup-sha256 HASH --serial YOUR_SERIAL
  ```

  Put the iPod in recovery mode when asked. If the dry run passes, run the same command
  with `--run` instead of `--dry-run` and type FLASH. Only `Firmware.MSE` is written;
  bootloader, music and files are not. The serial is in Settings > General > About.
  More: [docs/NANOAPPS-BASE.md](docs/NANOAPPS-BASE.md).

**3. Install the launcher and apps.** Put the iPod in Disk Mode and run:

```sh
./install              # stock iPod that got NanoApps from Finder; pick 1 if asked
./install --nanoapps   # iPod that came from nano7-untethered (after step 2)
```

Or double-click `Install nano7kit.command`. Type the numbers you want (for example
`1 4 21`), then Enter on an empty line. The Launcher is ticked by default. It backs up
`/Library` and `/Apps` first, then only copies files into `/Apps` and
`/Library/resident.hbapp`. Eject and restart the iPod.

**4. Add, remove, undo.** Run `./install` again: apps on the iPod show `[on]`, type a
number to remove one. Or, in Disk Mode:

```sh
./start nabase add 'Mario 64' --rom mygames/sm64.z64
./start nabase add 'PvZ Portable' --payload mygames/pvz-goty
./start nabase remove 'Paint'
./start nabase restore          # NanoApps' own launcher back
```

An empty file `/Library/disabled` on the iPod turns the launcher off.

**5. Music.** Sync in Disk Mode: `./start sync`, then Finder or Music. Never click
Restore. [docs/SYNC.md](docs/SYNC.md)

**If something goes wrong:** an app that keeps restarting the iPod can be removed with
`./install` (choose **r**) in Disk Mode. No Home screen: [docs/RECOVERY.md](docs/RECOVERY.md).
All commands: `./start --help`.
