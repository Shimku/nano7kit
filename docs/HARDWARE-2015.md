# Tested hardware

| iPod | Mac | Result |
|---|---|---|
| Mid-2015 nano 7G (MKN52, 16 GB) | Intel, macOS 13.5, through a USB-A hub | 1.0 on nano7-untethered, then 1.1 on NanoApps 1.0 (flashed with `nabase flash`): apps, music, Mario 64, PvZ Portable, Temple Run, Fruit Ninja and the PocketJS apps work |
| 2012 nano 7G, 16 GB | Linux (nano7-untethered) | Tested upstream, not with nano7kit |

The Mid-2015 model runs the same firmware 1.1.2 (39A10023) as the 2012 model but
shows up differently in recovery mode. `./install` asks once and then passes
`--i-accept-2015-refresh` for you. On the command line, firmware commands on a 2015
unit need that flag.

Notes from the tested unit:

- 1.1: `nabase flash --dry-run`, then `--run`, wrote NanoApps' firmware in recovery
  mode. The iPod booted with its music, then `./install --nanoapps` put the Launcher
  back. Probe still read 6.9 MB free and a 4.0 MB largest block with 14 apps on Home,
  and the four games and JS QuickJS opened and switched without a restart.
- Before that, on nano7-untethered, Finder's Check for Update failed (error 1461) and
  a Restore in recovery mode said no software was available. Nothing was erased.

- You can switch between the large games, the PocketJS apps and the NanoApps apps
  without the iPod restarting. JS QuickJS opens after other apps have run.
- A clean install from the public repo worked on an Intel Mac (macOS 13.5.1) with no
  Homebrew: `./install` set up the build tools in `build/macos-intel`, built and added
  Screenshot, and `./start apps remove 'JS Hero' 'JS Samples'` removed both. That was
  with the Launcher and 14 apps on Home, and switching between apps still worked.
- Removing apps from the `./install` menu is checked by unit tests only. Its Disk Mode
  step runs the same `./start apps remove` command that worked above.
- After a fresh boot with a full Home screen, Probe shows about 7 MB free and a
  4 MB largest block. Large games need that 4 MB block.
- On the tested Mac, recovery mode only worked through a USB-A hub, not a direct
  USB-C to Lightning cable.
- `untether --verify` reports CHECK CONDITION under this firmware. That is expected;
  the apps on the Home screen are the sign that it worked.
