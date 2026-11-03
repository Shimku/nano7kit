# FAT32 on a Mac

This is the nano7-untethered route, which nano7kit 1.0 used. The NanoApps base
([NANOAPPS-BASE.md](NANOAPPS-BASE.md)) needs none of it.

nano7-untethered needs the iPod's disk to be FAT32. A nano set up or restored on a Mac is
HFS+, and `./install` refuses it. `./start fat32` converts it without Windows.

This erases the iPod, music included. Sync your music again afterwards with
`./start sync` ([SYNC.md](SYNC.md)).

```sh
./start backup        # full image of the iPod, hash-checked; keep it on another disk
./start --i-accept-untested-flasher --i-accept-2015-refresh fat32 --dry-run \
  --serial YOUR_SERIAL --backup /path/to/nano.img --backup-sha256 HASH
```

When the dry run prints `DRY_RUN_OK`, run the same command with `--run` in place of
`--dry-run` and type `FAT32` when asked. The serial is in Settings > General > About.

What it does: puts the iPod in recovery mode ([BUTTONS.md](BUTTONS.md)), writes
Apple's stock 1.1.2 firmware from the official IPSW with the matching bootloader, lets
the iPod repartition itself, then formats the data partition FAT32. It never formats
the firmware partition.

This step was tested on one Mid-2015 unit and took several attempts to get right.
If the iPod stops on the Apple logo afterwards, read [RECOVERY.md](RECOVERY.md)
before trying anything else. `./start fat32 --scsi-dump DIR` is read-only and
shows what is on the disk.
