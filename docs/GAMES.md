# Games: bring your own file

nano7kit has no game data and never downloads any. You supply a file you own,
`./install` checks its hash and builds the game on your Mac. Put the file in `mygames/`
(git ignores it) or drag it into Terminal when asked.

| Game | File | Accepted | Port by | Status |
|---|---|---|---|---|
| Mario 64 | Super Mario 64 (USA) `.z64` | sha1 `9bef1128717f958171a4afac3ed78ee2bb4e86ce` | olievans123 | tested |
| PvZ Portable | the PC Plants vs. Zombies Game of the Year folder | `main.pak` sha256 `0cbdba7e…9d88cba` and `properties/` | PvZ-Portable (wszqkzqk) engine, nano7kit nano layer | tested |
| Temple Run | decrypted iOS `.ipa` or `.app` | `com.imangi.templerun` 1.0; the build also checks the armv7 binary hash | olievans123, with nano7kit memory changes | tested |
| Fruit Ninja | decrypted iOS `.ipa` or `.app` | `com.Halfbrick.Fruit` 1.0 | nano7kit | tested |
| Angry Birds | Android 1.0.0 `.apk` (or a zip of that APK) | `com.rovio.angrybirds` versionCode 100, armeabi-v7a `libangrybirds.so` sha256 `6d906957…704c95` | nano7kit | untested |
| Mario Kart 64 | Mario Kart 64 (USA) `.z64` | md5 `3a67d9986f54eb282924fca4cd5f6dff` | olievans123 | untested |
| Flappy Bird | iOS `.ipa` | version 1.2 | olievans123 | untested |
| Doodle Jump | iOS `.ipa` | version 1.0 | olievans123 | untested |

Temple Run is the same port NanoApps offers as experimental. nano7kit changes how it
takes memory so it gets past the loading screen with music on the iPod: it asks for
its whole memory block before falling back, draws less scenery when memory is low,
and uses smaller scenery textures (`host/templerun_patch.py`).

NanoApps also has optional Flappy Bird and Doodle Jump ports (in its `ports/`
folder), which it reports as working on a 2015 nano.

How each port was made, and how to port another game: [PORTING.md](PORTING.md).

Without the installer, in Disk Mode:

```sh
./start nabase add 'Mario 64' --rom mygames/sm64.z64
./start nabase add 'Fruit Ninja' --payload mygames/FruitNinja.ipa
./start nabase add 'PvZ Portable' --payload mygames/pvz-goty
./start nabase add 'Angry Birds' --payload mygames/angrybirds-android-1.0.0.apk
```

On the nano7-untethered layout use `./start apps add` with the same names
(`--ipa` for the iOS games). `./start apps add NAME --ipa FILE --plan` checks your
file, the tools and the memory on either layout and writes nothing.
