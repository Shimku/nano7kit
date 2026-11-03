# Recovery

Writing firmware can break an iPod for good. nano7kit takes a full, hash-checked
backup first, writes one thing at a time, and stops when a check fails. If
something looks wrong, stop and read before you retry.

Never use Restore in Finder or Music unless everything below has failed. It
erases the iPod. On nano7-untethered it also reformats it as HFS+, and you have to
run `./start fat32` again afterwards.

## An app keeps restarting the iPod

On the NanoApps base, put the iPod in Disk Mode ([BUTTONS.md](BUTTONS.md)), then:

```sh
./start nabase remove 'App Name'   # take that app off Home
./start nabase restore             # or put NanoApps' own launcher back
```

An empty file `/Library/disabled` makes NanoApps skip the launcher altogether.

On nano7-untethered, in Disk Mode, either:

```sh
./install                        # choose r
./start apps restore-tested      # back to Blocks, Game Boy and Probe
```

To put the original launcher back: `sudo -E ./start resident restore` (Disk Mode).

## Black screen, iPod only shows up in recovery mode

```sh
./start rescue --dry-run --serial YOUR_SERIAL
./start rescue --run --typed-confirm RESCUE --serial YOUR_SERIAL
```

This writes Apple's stock 1.1.2 firmware from the official IPSW and formats the data
partition FAT32. Your apps are gone until you run `./install` again.

## Apple logo, then nothing, after `./start fat32`

The firmware is on the iPod but the data partition is not readable. Format it again
without rewriting firmware:

```sh
./start --i-accept-untested-flasher --i-accept-2015-refresh --typed-confirm FAT32 \
  fat32 --run --format-only --serial YOUR_SERIAL
```

`./start fat32 --scsi-dump DIR` first if you want to see what is there. It only reads.

## Boot loop after installing the loader

Do not flash again. Run `./start rescue` as above. It writes stock firmware, not the
nano7kit one.

## `REFUSED:`

A `REFUSED:` line means a check failed before anything was written. Common reasons:
the iPod is HFS+, the disk size or backup hash does not match, the iPod is not the
one with that serial, or a Mid-2015 unit without `--i-accept-2015-refresh`.

## Backups

`./start backup` only reads the iPod. If you copy an image by hand, the direction is
always iPod to file:

```text
sudo dd if=/dev/rdiskN of=/other/disk/nano.img bs=4m conv=fsync
```

Never point `of=` at the iPod or at an internal disk.
