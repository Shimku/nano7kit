# Porting games and apps to the iPod nano 7G

All of this runs on the Mac. Only the final Disk Mode copy writes to the iPod. A port
takes the user's own copy of the game, checks it, and writes everything derived from it
to ignored folders (`build/`, `untether/artifacts/extra/Data/<Game>`). Don't commit ROMs,
`.ipa` files or game data. olievans123's port repos have no licence, so fetch them at a
pinned commit and credit him rather than copying them in.

From least to most work: a NanoApps app in C, a PocketJS app in TypeScript, or a native
port of a big game. The case studies below are in order of how well they run today.

## A NanoApps app

nano7kit's launcher is a fork of the NanoApps 1.0 launcher, so NanoApps apps run
unchanged. Follow their guide, [Developing Apps](https://github.com/nfzerox/NanoApps#developing-apps).
The C SDK is in `nanoapps/sdk/`.

```sh
./start sdk new my_app --title "My App"   # copies nanoapps/sdk/template (direct framebuffer)
./start sdk build my_app
./start nabase add "My App"               # Disk Mode
./start package my_app                    # or a .nanoapp zip in nanoapps/build/packages/
```

`nanoapps/apps/lap` is a small example. Someone else's `.nanoapp` folder (it holds
`manifest.napp`, `app.hbapp` and `icon.bin`) can be copied into `/Apps` in Disk Mode.
NanoApps advises keeping an app under about 0.5 MB. Bigger ones need the parts below.

## A JavaScript app

See [POCKETJS.md](POCKETJS.md).

## A big native game

### What you are porting to

- One app runs at a time. The launcher loads its `.hbapp` into one OS heap block.
  Anything large the game allocates should go in `HB_APP_AUX_BLOCK`
  (`nanoapps/sdk/hb_heap.h`) so the launcher frees it on the way back to Home.
- On the tested nano, after a fresh boot with 14 apps on Home, Probe shows about
  6.9 MB free and a 4.0 MB largest block. Music and Bluetooth take more. The graphics
  driver needs about 3 MB of what is left.
- OpenGL ES 1.1 through the NanoApps SDK (`nanoapps/sdk/gl.h`). The driver has rules
  that restart the iPod when broken: `./start port rules` lists them. Temple Run
  measured about 3,000 bytes of driver memory per triangle (`host/templerun_patch.py`).
- Floating-point code has to be built in ARM state: this firmware traps VFP use from
  Thumb code (`games/pvz_portable/engine/build_arm.py`, `games/fruitninja/nano/fn_jmp.S`).
- `hb_fs_read` reads a whole file from the start, so big data files are split into
  pieces at build time (`games/fruitninja/nano/plat_nano.c`).
- Screen: 240x432 touch, plus an accelerometer. No port here plays sound yet.

### The method

1. **Pin the input.** Accept one exact build of the game and check it: bundle id,
   version, executable hash, and that it is decrypted (`cryptid` 0). A recompiler
   reads addresses from one build, so any other is refused
   (`inspect_payload` in `host/fruitninja_rc.py` and `host/templerun.py`).
2. **Pick the route.** If there is source (an open engine or a decompilation), port
   its platform layer: Mario 64 and PvZ Portable. If there is only the iOS binary,
   recompile it to C: Temple Run and Fruit Ninja. Each original function becomes a
   C function, registers become locals, guest memory is one block, and calls into
   iOS libraries go to a small runtime you write.
3. **Run it on the Mac first.** A host build with a software renderer and scripted
   input shows whether it works and how much memory it needs: heap peak, texture
   bytes, triangles in the busiest frame.
4. **Make it fit.** Take one block sized from the measured peak. Before starting,
   check the largest free block and show a NOT ENOUGH MEMORY screen instead of letting
   the OS allocator restart the iPod (`games/fruitninja/nano/notice.c`). Set the
   triangle budget from free memory and shrink textures if needed.
5. **Clean up on exit.** The launcher keeps one GL context for all GL apps, so
   textures a game created outlive it. Record your texture names and delete what an
   earlier port left behind (`games/fruitninja/nano/gl_left.c`).
6. **Check before writing.** `./start apps add 'Fruit Ninja' --ipa FILE --plan`
   (also Temple Run, PvZ Portable and Gravity Defied with `--payload`) checks the file, the tools and
   the memory, and writes nothing. `./start port budget FILE.hbapp` checks the size.

### Mario 64 (N64, olievans123's port)

Files: `scripts/build-games.sh`; the port itself is in nano7-untethered `games/sm64`.

- **Chain:** the n64decomp decompilation, jnmartin84's Dreamcast port
  [sm64-dc](https://github.com/jnmartin84/sm64-dc) at `6be7a000`, then olievans123's
  `nano-port.patch`: an OpenGL ES 1.1 renderer that batches by texture and stays under
  about 80 live textures, touch and tilt input, a host test build, and game data split
  into files read as needed (nano7-untethered `games/README.md`).
- **nano7kit's part:** build it on a Mac (C++17 for armips, `clang -E` as the
  preprocessor, a libzstd fix for the Arm toolchain, portable `sizes.txt`), check
  your USA ROM (sha1 `9bef1128...`), and rebuild when the patch or SDK changes. The
  roughly 3 MB image loads through the launcher's large-app path.
- No sound, as upstream.

### PvZ Portable (PC game files, PvZ-Portable engine)

Files: `games/pvz_portable` (LGPL-3.0-or-later), `host/pvz_portable.py`. This is not
an iOS port: it uses the PC Game of the Year `main.pak` and `properties/` folder.

- **Engine:** [PvZ-Portable](https://github.com/wszqkzqk/PvZ-Portable) by wszqkzqk at
  `98b65938`, fetched with zlib 1.3.1, the SDL 2.30.9 headers and dlmalloc 2.8.6,
  each checked by SHA-256. nano7kit's patch and device layer go on top.
- **Repacker** (`repack/repack.py`): reads your `main.pak`, turns every animation into
  a compact `.n7r` file, drops resource groups the nano never loads, scales images to
  16-bit `.n7t` textures at 0.4, and stores each file on its own because the iPod
  reads whole files. No sounds are copied.
- **Device layer** (`engine/nano7dev`): one dlmalloc arena in the app's aux block
  (`n7alloc.c`) and an OpenGL ES 1.1 renderer. The driver takes a few hundred
  triangles a frame and a plant is 10 to 40 parts, so plants are drawn once on the
  CPU into one small picture (`engine/nano7/n7bake.cpp`). An earlier build that made
  a new texture every few walk frames restarted the iPod; poses are now still pictures.
- **Dry run:** an x86 build with a software renderer and a bot that plays the
  adventure levels by itself (`engine/nano7/bot.cpp`) measured the arena it needs:
  3.0 MB for 1-1, 3.2 MB through 1-10.
- The 800x600 game is drawn at 0.4 with the iPod held sideways (`nano/pvz_nano.c`).

### Temple Run (iOS, olievans123's port with nano7kit memory changes)

Files: `host/templerun.py`, `host/templerun_patch.py`.

- **Port:** [nano-templerun](https://github.com/olievans123/nano-templerun) at
  `b73a3d43` (`host/ports.py`), fetched at build time. It recompiles the armv7 half
  of Temple Run 1.0 (`91d85b35...afe01392`, 1,439,184 bytes) to C and converts your
  textures and models.
- **Why the changes:** with 11 apps and music, the heap read about 6.6 MB free and
  4.0 MB largest. The port's arena clamp then gave it about 1.7 MB, the engine
  needs about 3.1 MB, and it stopped under the splash screen.
- **The changes** (applied to the fetched tree, each anchored on a line of the pinned
  commit, so a moved pin stops the build): ask for the full arena first, stepping down
  64 KB at a time to a 3.125 MB floor; a triangle budget of 400 to 1,300 from live
  free memory; the three 1024 scenery sheets at 256; a memory pre-flight with the
  NOT ENOUGH MEMORY screen; and the shared texture clean-up from Fruit Ninja.
- NanoApps lists the same port as experimental: it ran out of memory at the loading
  screen on their fully loaded test device (NanoApps `tools/build_ios_game.py`).

### Fruit Ninja (iOS, recompiled by nano7kit)

Files: `games/fruitninja`, `host/fruitninja_rc.py`. The approach follows
olievans123's Temple Run port; none of its code is used.

- **Input:** decrypted Fruit Ninja 1.0, `com.Halfbrick.Fruit`, thin armv6 executable
  `67754af4...bd7d61`.
- **Recompiler** (`tools/recomp.py`, `tools/macho.py`): Thumb to C, starting from the
  engine's `IPhoneInterface` entry points (start-up, update, draw, touch). OpenFeint,
  Facebook and OAuth code is left out. The music call is stubbed, so it is silent.
- **Runtime** (`rt/`): guest memory and dispatch (`rt_core.c`), the iOS calls the
  engine makes (`rt_imports.c`; the 8.6 MB `fileArcive.bin` is never loaded whole),
  and OpenGL ES 1.1 state with transform and clipping on the CPU (`fngl.c`).
- **Nano side** (`nano/`): the frame loop takes one 2.4 to 3.0 MB guest block
  (`fn_nano.c`); the landscape game fills the panel with the iPod held sideways;
  `be_nano.c` batches triangles and drops ones the driver can't take;
  `plat_nano.c` reads the archive in 128 KB pieces.
- **Host harness** (`host/`): runs the engine headless with a software rasterizer,
  writes frames and plays a scripted swipe. Measured there: guest heap peak
  1,075,216 bytes, at most 691 triangles a frame.
- **Touch, learned on hardware** (`nano/fn_nano.c`, `rt/fn_entry.c`): the game reads
  the OS touch queues, plus the launcher's per-event snapshot
  (`nanoapps/system/silver_resident_na/touch_latch.h`) because the OS empties the queues
  after each touch event. The engine kills the blade for the rest of a touch once a held
  finger reports no movement for two update steps, and NanoApps delivers some frames with
  no new sample. So a frame with nothing new is skipped instead of repeating the old
  position, and a lift is registered even when an earlier entry in the queue was a down.
  The host harness showed the blade dying; `tests/test_fruitninja_rc.py` checks the fix.
- **Writes:** `Fruit Ninja.hbapp` (about 920 KB) and `/Apps/Data/FruitNinja` (about 9.2 MB).

### Gravity Defied (J2ME, rgimad/gravity_defied_cpp)

Files: `games/gravity_defied` (GPL-2.0), `host/gravity_defied.py`.

- **Engine:** [gravity_defied_cpp](https://github.com/rgimad/gravity_defied_cpp) by rgimad,
  AntonEvmenenko and Max Logaev at `91bd283`, GPL-2.0, fetched at build. Original
  J2ME game by Codebrew (Tors, Set, Per), 2004. Game data (`levels.mrg`, sprites)
  comes from your own jar and is never committed.
- **Device layer:** RAW_SURFACE framebuffer (240x432 XRGB8888), an iOS-style 3x3
  phone keypad (J2ME keys 1-9) under a 3 px green progress line, accelerometer
  lean, dlmalloc arena in `HB_APP_AUX_BLOCK`, a small Inter OFL bitmap font,
  silent. Float code is built in ARM state (`-marm`, VFPv4-D16).
- **Upstream edits at pin:** cap the bike-part loop in `GamePhysics::method_39`,
  `RecordEnumerationImpl::destroy()` is a no-op so progress saves, menu `TextRender`
  objects are deleted, blocking `Micro::run` / `goalLoop` / `MenuManager::method_201`
  wait loops become ticks, exceptions and `std::filesystem` are removed.
- **Pre-flight:** the same NOT ENOUGH MEMORY screen as Fruit Ninja, without malloc.
  Dry run: `./start nabase add 'Gravity Defied' --payload FILE --plan`.
- **Writes:** `Gravity Defied.hbapp` and `/Apps/Data/GravityDefied` (converted sprites,
  `levels.mrg`, `log.txt`, RMS saves).

## Adding a game to nano7kit

Follow how PvZ Portable is wired in (`rg "PvZ Portable" host`):

- a build recipe `host/<game>.py`, called from `prepare_for_add` in `host/ports.py`;
- the name in `LARGE_GL` and `NEEDS_PAYLOAD` (or `NEEDS_ROM`) in `host/ports.py`;
- a row in `host/app_catalog.py`;
- a menu entry in `host/installer.py`, with `tested=False` until it has run on a nano;
- tests in `tests/`, and credit in `CREDITS.md`.

`./start port families` and `./start port status` print the steps for each kind.

If a game restarts the iPod: `./start apps lasttrace` reads the launcher's trace from
the previous boot (Disk Mode, read-only), and `./start nabase remove NAME` takes it off Home.
