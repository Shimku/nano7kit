# nano7kit

Install apps, games and JavaScript apps for the iPod Nano 7th generation from a Mac.

![Super Mario 64 running on four iPod nano 7G units](docs/images/mario.png)

nano7kit wouldn't be possible and is built on top of [NanoApps 1.0](https://github.com/nfzerox/NanoApps) by nfzerox and
[nano7-untethered](https://github.com/olievans123/nano7-untethered) by olievans123. It keeps
NanoApps' firmware & 40+ apps selection and adds:

- Console, PC and iOS games that can be ported to iPod. Some examples of things that work are Plants vs. Zombies (PC, my port of the PvZ-Portable engine), Super Mario 64 (N64,
  olievans123's port), Fruit Ninja (iOS, my
  recompile). 
- [PocketJS](https://github.com/pocket-nexus/pocketjs): write nano apps in TSX and run them
  on QuickJS on the iPod, with touch gestures and tilt
- a fork of the NanoApps launcher for big apps: the patched SDK checks the largest free
  block before allocating and reads files over 512 KB in pieces; on refresh it unloads
  before reading the app catalog, and it only re-reads the catalog after USB marked the
  disk dirty

## Before you start

- **iPod:** nano 7G on firmware 1.1.2. I tested one Mid-2015 model (MKN52, 16 GB). The
  2012 model should run the same, but I haven't tried it.
- **Mac:** I tested an Intel Mac on macOS 13.5. On Intel, `./install` downloads its own
  Python, Arm compiler and other tools into `build/macos-intel` (no Homebrew, no
  password). Apple M-series should work with Homebrew ([docs/MACOS.md](docs/MACOS.md)) but
  I haven't tested it on that hardware. You also need Xcode's command line tools
  (`xcode-select --install`). No Windows support but NanoApps should get you covered there.
- **Cable:** Lightning to USB-C or USB-A. My Mac only saw the iPod in recovery mode through a USB hub.
- **Time:** tool setup takes a few minutes. Heavier games like Mario 64 take several
  minutes to build.
- **Risk:** on the NanoApps base, nano7kit only copies files into `/Apps` and
  `/Library/resident.hbapp`, after backing up `/Library`, `/Apps` and `/NanoApps` to your
  Mac. Music is not touched. Writing firmware (step 1b below) is riskier: it takes a full
  disk image first and refuses if anything doesn't match. See [Undo](#undo).
- **Apps & Games:** nano7kit has a porting guide, and the four games
  below are tested on hardware. You have to supply your own game files, for obvious reasons
  (table below).

## Install

Already on NanoApps 1.0? Put the iPod in Disk Mode ([docs/BUTTONS.md](docs/BUTTONS.md)) and:

```sh
git clone https://github.com/shimku/nano7kit.git && cd nano7kit && ./install
```

Otherwise see [Full setup](#full-setup) at the bottom.

![The Home screen with NanoApps apps, games and PocketJS](docs/images/home.png)

## Games (and Apps)

![Temple Run, Blocks, Plants vs. Zombies and Fruit Ninja on the nano](docs/images/games.png)

| Game | You supply | Port |
|---|---|---|
| PvZ Portable | the PC Plants vs. Zombies GOTY folder (`main.pak` and `properties/`) | PvZ-Portable engine downscaled for Nano, `games/pvz_portable` |
| Super Mario 64 | Super Mario 64 (USA) `.z64` | olievans123's |
| Temple Run | decrypted Temple Run 1.0 `.ipa` | olievans123's, with my memory changes |
| Angry Birds | Angry Birds 1.0 `.ipa` | olievans123 engine reimplementation; untested in nano7kit |
| Fruit Ninja | decrypted Fruit Ninja 1.0 `.ipa` | recompiled here, `games/fruitninja` |
| Other | Mario Kart, Blocks, Flappy Bird, Doodle Jump, etc. ) | Blocks runs great, other games were not tested but should run |

Put the files in `mygames/` (git ignores it) or drag them into Terminal when the
installer asks. Each file is hash-checked, so only those exact versions work. Nothing
built from them is committed. Hashes and the untested extras (Mario Kart 64, Flappy
Bird, Doodle Jump): [docs/GAMES.md](docs/GAMES.md).

## Writing apps

The nano gives an app a 240x432 touch screen, an accelerometer, OpenGL ES 1.1 and about
4 MB of memory. Due to memory constraints, one app runs at a time, but saving is possible where available (e.g. PvZ or PocketJS).

In C, with the NanoApps SDK (NanoApps apps run unchanged on this launcher):

```sh
./start sdk new my_app --title "My App"
./start sdk build my_app
./start nabase add "My App"        # Disk Mode
```

Porting a big game: [docs/PORTING.md](docs/PORTING.md) walks through Mario 64, PvZ,
Temple Run and Fruit Ninja.

## PocketJS

![PocketJS apps on the nano: a counter, the home screen, Motion Lab and Notes](docs/images/pocketjs.png)

[PocketJS](https://github.com/pocket-nexus/pocketjs) is pocket-nexus's framework for
writing small-screen apps in Solid or Vue TSX. nano7kit ports it to an iPod Nano 7G and it is pretty damn cool.

You write an app as a component, lay it out with flexbox and Tailwind-style classes,
and keep state in signals. It can read two-finger touch and tilt. On the iPod it runs
on QuickJS, or you can compile it ahead of time with MicroTS.

Each app is one `.pocket` file in `/Apps/Data/PocketJS`. Run `./start pocketjs emu`
to try it on your Mac first. It also shows the app's peak memory use.

An example app, and how to build and add your own: [docs/POCKETJS.md](docs/POCKETJS.md).
PocketJS is fetched at `6ca9f914` and patched with `pocketjs/nano7g.patch`.

## Known issues

No games have sound. I haven't tried every NanoApps app but those I did work flawlessly so I don't see a reason others would break. Mario Kart 64, Flappy Bird and
Doodle Jump are untested here but should be working. Making the install a bit more graphical would be nice. Of course, this should be tested on more iPods.

## Who brought what

- **nano7-untethered** (olievans123) made homebrew untethered: a patched firmware (built
  with nfzerox's ipod_sun_untethered, flashed from recovery mode with wInd3x on a Linux PC)
  loads apps at every boot with no computer attached. It needs a FAT32 disk, which means
  an iPod formatted by iTunes on Windows, and erasing it if it isn't. It ships Blocks, Game
  Boy, Probe and native ports of Mario Kart 64 and Super Mario 64 built from your ROMs.
  Tested on one 2012 nano. On my nano, Finder could no longer update the iPod while it ran
  this (error 1461). nano7kit 1.0 was built on it by porting the entire process over to Mac and combining it with an earlier NanoApps release.
- **NanoApps 1.0** (nfzerox) is the app framework: 40+ apps, a C SDK (LVGL widgets,
  OpenGL ES 1.1 or a raw framebuffer) and the launcher. 1.0 installs as an IPSW for the 2012
  or 2015 model through Check for Update in Finder, iTunes or Apple Devices, on Mac or
  Windows, with no Terminal. It includes olievans123's untethered loading, Merquice's
  AirPods H2 fix, Raphmatt's Lightning Dock fix and ninjaprawn's music sync fix. After
  you go back to Home, a 500 ms timer unloads the last app. A relaunch before that
  timer can reuse the still-loaded image. Opening a different app unloads the others
  immediately.
- **nano7kit 1.1** (this) adds some things on top of NanoApps 1.0: the launcher fork that fits big
  games; new ports for Plants vs. Zombies and Fruit Ninja alongside existing apps (e.g. Probe) and games (e.g. Super Mario 64,
  etc.) with memory changes to some games (e.g. Temple Run);
  the PocketJS Nano 7G target for building & running JS/TSX apps; a Mac-first installer; and
  `nabase flash`, which moves a nano from nano7-untethered to NanoApps without erasing it.
  Mac only, needs Terminal, tested on one Mid-2015 nano.

**What you end up with:** NanoApps' firmware and apps and games you select incl. Game Boy, Mario 64, PvZ,
Temple Run, Fruit Ninja and the PocketJS apps on the same Home screen, switching without a
restart. Music doesn't get removed, and obviously you can add apps and games you create or port yourself.

The nano7kit 1.0 route on nano7-untethered is still here as a fallback
([docs/FAT32.md](docs/FAT32.md)).

## Full setup

Clone the repo (`git clone https://github.com/shimku/nano7kit.git && cd nano7kit`), then:

**1. Put NanoApps 1.0's firmware on the iPod.** Music and files stay.

a. *Stock iPod:* `./start nabase ipsw` downloads `NanoApps-2015-Model.ipsw` and checks it
(`--model 2012` for the 2012 nano). In Finder, select the iPod, hold Option, click
**Check for Update** (not Restore) and pick that file. These are NanoApps'
[own steps](https://github.com/nfzerox/NanoApps#download-and-install-nanoapps).

b. *iPod on nano7-untethered (Finder says error 1461):* write the same firmware from
recovery mode. This is the route I tested. In Disk Mode, back up first:

```sh
./start backup     # full disk image; prints its path and SHA-256
./start --i-accept-untested-flasher --i-accept-2015-refresh nabase flash --dry-run \
  --backup IMAGE --backup-sha256 HASH --serial YOUR_SERIAL
```

Put the iPod in recovery mode when asked. If the dry run passes, run it again with
`--run` and type `FLASH`. It writes only `Firmware.MSE`. The serial is in Settings >
General > About. More: [docs/NANOAPPS-BASE.md](docs/NANOAPPS-BASE.md).

**2. Install.** In Disk Mode:

```sh
./install              # after 1a; pick 1 if it asks which way
./install --nanoapps   # after 1b
```

(Or double-click `Install nano7kit.command`.) Type the numbers you want, for example
`1 4 21`, then Enter on an empty line. Eject and restart the iPod.

**3. Add and remove later.** Run `./install` again: installed apps show `[on]`, type the
number to remove one. Or in Disk Mode:

```sh
./start nabase add 'Mario 64' --rom mygames/sm64.z64
./start nabase add 'PvZ Portable' --payload mygames/pvz-goty
./start nabase remove 'Paint'
```

Sync music in Disk Mode only: `./start sync`, then Finder or Music, never Restore
([docs/SYNC.md](docs/SYNC.md)).

## Undo

- NanoApps' own launcher back: `./start nabase restore` (Disk Mode). Your music and the
  NanoApps apps stay.
- Launcher off without a Mac command: create an empty file `/Library/disabled` on the iPod.
- Back to stock: Restore in Finder erases the iPod and installs Apple's firmware. I
  haven't tried that after `nabase flash`. If Finder can't, `./start rescue` writes stock
  1.1.2 from recovery mode ([docs/RECOVERY.md](docs/RECOVERY.md)).
- Firmware backup: the image from `./start backup` stays on your Mac.

## Troubleshooting

- **Finder: "error 1461" on Check for Update.** The iPod runs nano7-untethered. Use step 1b.
- **The iPod doesn't show up in Disk Mode.** Wait for "Do not disconnect" on screen
  ([docs/BUTTONS.md](docs/BUTTONS.md)), try another cable or port, then `./start find`.
  If you renamed the iPod, `./start nabase` commands take `--volume /Volumes/NAME`.
- **Recovery mode isn't detected.** Try a USB-A hub. My Mac needed one.
- **`REFUSED: did not type FLASH; nothing written` right after the prompt.** The prompt
  got an empty line, usually from pasting a command with a newline after it. Paste the
  command alone, wait for the prompt and type the word yourself.
- **`REFUSED:` line.** A check failed before anything was written. The message says which.
- **An app restarts the iPod.** In Disk Mode, `./start nabase remove 'App Name'`. To see
  where it died, restart normally to the Home screen, then Disk Mode and
  `./start apps lasttrace`.
- **PocketJS app misbehaves.** `./start pocketjs log` in Disk Mode prints its log.
- **Tools missing.** `./start doctor` lists what's missing.

To report a bug, open an [issue](https://github.com/Shimku/nano7kit/issues) with your
iPod model, Mac (Intel or Apple silicon, macOS version), the command you ran and its full
output. For crashes, add `./start apps lasttrace` or `./start pocketjs log`.

## Credits

Built on [NanoApps](https://github.com/nfzerox/NanoApps) by nfzerox (MIT), with fixes by
Merquice and Raphmatt; [nano7-untethered](https://github.com/olievans123/nano7-untethered)
and the Temple Run port by olievans123 (no licence stated, so fetched at build time, not
included); [PvZ-Portable](https://github.com/wszqkzqk/PvZ-Portable) by wszqkzqk
(LGPL-3.0); [PocketJS](https://github.com/pocket-nexus/pocketjs) by pocket-nexus (MIT,
pinned at `6ca9f914`) with Motion Lab by yui540; QuickJS; [Inter](https://rsms.me/inter/)
(OFL); wInd3x; ipod_sun. Everyone, with licences: [CREDITS.md](CREDITS.md),
[NOTICE](NOTICE), [LICENSES/](LICENSES/). The old
untethered recovery, `./start apps restore-tested`, is in [docs/RECOVERY.md](docs/RECOVERY.md). Cursor and GrokBot saved me here.

Unofficial and at your own risk. Not affiliated with Apple, Nintendo, Imangi, Halfbrick,
PopCap or any game developer. No game files, ROMs or Apple firmware are included. MIT,
see [LICENSE](LICENSE); some parts keep their own licence, see [NOTICE](NOTICE).
