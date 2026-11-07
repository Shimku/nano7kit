# nano7kit on the NanoApps 1.0 base

New in 1.1. I tested it on one Mid-2015 nano (MKN52, 16 GB) that came from
nano7-untethered: `nabase flash`, then `./install --nanoapps`. Music survived and
Probe still read 6.9 MB free and a 4.0 MB largest block. Not tried yet: a nano that got
NanoApps from Finder, a Mac-formatted disk, Finder music sync with this launcher, and
Finder's Check for Update after the flash. The nano7-untethered route
([FAT32.md](FAT32.md)) still works.

NanoApps 1.0 installs as a Finder update. Its firmware loads
`/Library/resident.hbapp` from the iPod's disk if the file's checksum, ABI 2,
firmware 1.1.2 and size check out. nano7kit puts its own Launcher there (the same
code as on the untethered path, with NanoApps' version trailer) and copies its
apps into `/Apps`. Nothing else changes: no firmware write, no DFU, no FAT32
conversion, no erase, no Mac password. Mac-formatted and FAT32 disks both work.

## Install

1. Install NanoApps 1.0: `./start nabase ipsw` downloads `NanoApps-2015-Model.ipsw`
   and checks its SHA-256. In Finder, select the iPod, hold Option, click
   **Check for Update** (not Restore) and pick that file.
2. Put the iPod in Disk Mode and run `./install` (or double-click
   `Install nano7kit.command`). Pick 1 when it asks which way to go.
   If the iPod still has nano7-untethered files, run `./install --nanoapps`.
3. Eject and restart the iPod.

`./install` copies a file backup of `/Library`, `/Apps` and `/NanoApps` to
`build/nanoapps-base-backups` before it writes. It never writes outside `/Apps`
and `/Library/resident.hbapp`, so music in `iPod_Control` is not touched. On a
fresh base it also copies NanoApps' own disk files (step 3 of their README) and
never replaces a file that is already there.

### If Finder cannot update the iPod

On an iPod running nano7-untethered, Finder's Check for Update fails (error 1461)
and Restore in recovery mode says no software is available (seen on the tested
unit). The likely reason, not proven: nano7-untethered's loader overwrites the
SCSI command handler (C6/96) Finder uses, while NanoApps keeps it (their
`firmware/src/payload/exploit_config.rs`). Use the same recovery-mode
firmware write that put nano7-untethered on the iPod, with NanoApps' firmware:

```sh
./start --i-accept-untested-flasher --i-accept-2015-refresh nabase flash --dry-run \
  --backup IMAGE --backup-sha256 HASH --serial YOUR_SERIAL
./start --i-accept-untested-flasher --i-accept-2015-refresh nabase flash --run \
  --backup IMAGE --backup-sha256 HASH --serial YOUR_SERIAL
```

It writes only the `Firmware.MSE` from the pinned `NanoApps-2015-Model.ipsw`
(Apple's 1.1.2 firmware with NanoApps' resource files). Bootloader, partitions,
music and files are not written. Put the iPod in recovery mode (DFU) for each
step. The dry run writes nothing, `--run` asks you to type FLASH, and each image
is written once only. Then run `./install --nanoapps` in Disk Mode.

`IMAGE` and `HASH` are the full backup from `./start backup` and its SHA-256. The
serial is in Settings > General > About. The flasher is wInd3x (freemyipod),
with nano7-untethered's patch and nano7kit's Mid-2015 changes to it
(`upstream/nano7-untethered.patch`).

## What to expect

- Copying `NanoApps-disk.zip` onto the iPod again, or a later NanoApps release,
  puts their launcher back. Your apps stay; run `./install` again.
- Dragging their `Apps` folder onto the iPod in Finder can replace yours,
  saves included. Use `./start nabase disk-files` instead: it only adds files.
- Home shows NanoApps' `.nanoapp` folders and nano7kit's apps, up to 60.
  Our Launcher keeps its memory fix (it does not grow the icon cache), so more
  than 16 apps with it is untested on hardware. Remove folders in the menu if needed.
- ninjaprawn's music sync fix is in NanoApps' firmware. Finder sync with
  nano7kit's Launcher is untested; Disk Mode sync (docs/SYNC.md) still works.

## Undo

- NanoApps' own launcher back: `./start nabase restore` (Disk Mode). It uses the
  backup, or the pinned `NanoApps-disk.zip` if there is none.
- No launcher at all: create an empty file `/Library/disabled` in Finder while in
  Disk Mode. Both NanoApps stages skip loading while it exists.
- Status: `./start nabase status`.

## Adding and removing apps here

On this base use `./start nabase add` and `./start nabase remove` (or `./install`).
`./start apps add` and `./start apps remove` are for the nano7-untethered layout.
`nabase add` builds a game from your file first if it is not built yet or the
source changed, then copies it, replacing the old copy:

```sh
./start nabase backup
./start nabase add 'Fruit Ninja' --payload mygames/FruitNinja.ipa
./start nabase add 'Angry Birds' --payload mygames/AngryBirds.ipa
```

## Commands

`./start nabase status | backup | launcher | restore | disk-files | packages`,
`add NAME [--rom FILE | --payload FILE]`, `remove NAME`, `mark nanoapps|untethered`,
`ipsw [--model 2015|2012]`, `flash --dry-run|--run`. All but `ipsw` and `flash` take `--volume /Volumes/NAME`.
