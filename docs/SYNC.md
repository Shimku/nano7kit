# Music

nano7kit never writes the music library. Finder, Music or iTunes do that.

Sync only in Disk Mode. On nano7-untethered, Finder or Music talking to the iPod
while it is on the Home screen can make it restart in a loop. NanoApps' firmware
keeps the USB command Finder uses, but Finder sync with nano7kit's Launcher on it
is untested, so Disk Mode is still the safe way.

1. Quit Music and close any Finder window for the iPod.
2. Put the iPod in Disk Mode ([BUTTONS.md](BUTTONS.md)).
3. Run `./start sync`. It checks the iPod and records your apps.
4. In Finder or Music, select the iPod and sync. Prefer "Manually manage music".
   Do not click Restore.
5. Eject, unplug, and restart with Sleep/Wake and Home.

`./start sync` checks the apps again afterwards so a Restore cannot remove them
without you noticing.

If the iPod shows 0 songs or Finder says it cannot read the contents, the library
file is damaged. Put it back from your backup in Disk Mode:

```sh
./start library restore-from-backup --backup IMAGE --backup-sha256 HASH
```

Do not use Linux tools that write the music database (gtkpod, libgpod, Rhythmbox).
One sent a test iPod into a boot loop.
