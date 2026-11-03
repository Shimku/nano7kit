# Safety checks

nano7kit refuses when it is unsure. These checks run every time:

- The firmware image must match the hardware-tested SHA-256.
- A full backup must exist, match the iPod's size and hash, and is re-checked before
  every write.
- Recovery mode must report a Nano 7G with the serial you typed. Mid-2015 units also
  need `--i-accept-2015-refresh`.
- Each firmware image can be written once. A second attempt with the same image is
  refused.
- Live writes need a typed confirmation word (`FLASH`, `FAT32`, `UNTETHER`, `RESCUE`, `BOOTLOADER`).
- The nano7-untethered path refuses HFS+ disks. See [FAT32.md](FAT32.md).
- On the NanoApps base, the installer copies a backup of `/Library`, `/Apps` and
  `/NanoApps` to your Mac first and writes only `/Apps` and `/Library/resident.hbapp`.
- The music folders (`iPod_Control`, `iTunes_Control`) are never written, except
  restoring the library file from your own backup.
- App copies are flushed, remounted and re-hashed before the iPod is unplugged.
- Only one nano7kit command can write at a time.
- Downloads are pinned to a commit and checked against a hash list
  (`upstream/lock.json`, `UPSTREAM.txt`).

`./start verify-release` checks the release files against their hashes.

Disk sizes: the tested sizes are 15,798,411,264 bytes (as set up by Finder) and
15,806,623,744 bytes (after `./start fat32`). Other sizes need
`--allow-disk-size BYTES` and `--confirm-untested-capacity`.
