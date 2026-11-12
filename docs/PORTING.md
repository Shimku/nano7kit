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
   (also Temple Run and PvZ Portable with `--payload`) checks the file, the tools and
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

### Angry Birds (iOS, recompiled by nano7kit)

Files: `games/angrybirds`, `host/angrybirds.py`. Same approach as Fruit Ninja
(olievans123-style static recompile; none of that tree's code is used).

- **Input:** decrypted Angry Birds 1.0, `com.clickgamer.AngryBirds`, thin armv6
  executable `e1dadaf6...6ec6e8` (1,822,112 bytes). All 2281 text functions are
  ARM with VFPv2, not Thumb.
- **Recompiler** (`tools/recomp.py`): ARM+VFP to C, starting from
  `framework::init`, `GameApp::update` and the App touch methods. The ObjC
  shell (`AppController`, `MyEAGLView`) is left out. OpenAL is stubbed, so it
  is silent. Ka3D SjLj is a host `jmp_buf` around `__Unwind_SjLj_Register`.
- **Runtime** (`rt/`): guest memory (`rt_core.c`), iOS calls (`rt_imports.c`),
  GLES 1.1 with CPU clip (`fngl.c`). Landscape 480x320 fills the 240x432 panel
  with Home on the right, same map as PvZ `n7gl_dev_a.inc`: panel x = GL fy *
  0.75, panel y = game x * 0.9 (logical top sits at portrait RIGHT). Touch is
  the inverse (`Nano7PanelToGame`). Host PPM is 432x240 via PvZ `n7soft_shot`
  (`px = 239-gy, py = gx`) so a landscape screenshot matches the iPod.
- **Assets** (`tools/convert_assets.py`): wav/mp3 skipped; INGAME sheets keep
  max side 256. MENU, SPLASHES and TUTORIALS keep 512 (one fewer halve than
  1024-class sheets at 256). Every PNG is rewritten as a POT-padded PVR
  RGBA4444 file (`MENU.png` -> `MENU.pvr`) and the sheet `.dat` filename is
  patched in place (same 8-byte length). Ka3D picks PNG vs PVR from the
  extension. That is so the runtime never allocates a 400-515 KB RGBA8
  decode (c9880d1 OS heap after the guest block cannot). MENU_1 (hills) and
  the other UI sheets stay 256 on a side. Original 1024 sheets cannot upload
  (nano `be_tex_image` cap 512). Sprite `.dat` pixel sizes stay original;
  UV wrap is undone from `texscale`.
  Lua 5.1 bytecode is copied as-is. Converted files stay in ignored `build/`
  and `Data/AngryBirds`; none are committed.
- **Nano side** (`nano/`): most of the translated engine is Thumb + software
  float so the loaded image stays about 2.2 MiB (ARM+VFP for everything was
  3.2 MiB text). Box2D in `recomp_14.c`/`recomp_15.c` is ARM+softfp+VFP like
  Fruit Ninja. Runtime, GL and `setjmp` stay ARM+VFP. On-device log:
  `/Apps/Data/AngryBirds/log.txt` (rewritten whole on each flush, then
  `sync.txt` with the last line, like PvZ). `plat_log_step` flushes so a
  LEVEL line is on disk before the next frame. Each `LEVEL` line has name,
  camera scale/bounds, textures loaded/failed, heap used/hole; lua errors as
  `luaerr` / `game:`. `ab_watch_level` writes those lines on every
  `levelName` change (including in-game `loadNextLevel`).
  Guest.img maps only `__cstring` through DATA (~80 KB). The 240x432 picture
  is fixed like Fruit Ninja (`fe_scale` 0.75/0.9, `fe_off` 0); only the game's
  camera moves. Two fingers are ids 0 and 1 so native pinch zoom and double-tap
  reset work. Volume keys are unused.
- **Host harness** (`host/`): headless software raster, same 512 texture cap
  as the nano, the same 2 MB guest block (`BLOCK_MAX`), and the same
  home-on-right landscape dump as PvZ (`P6 432 240`, `px=239-gy, py=gx`).
  `AB_NO_OUTSIDE=1` reproduces the device MENU.png 515100-byte new[] OOM
  (guest first-fit, no 515 KB hole). Decode buffers of 256 KB or more use
  `plat_os_alloc` (nano: guest address `host_ptr - g_mem` so `AT` stays
  `M+a`; host: `0xB0000000` slabs). `AB_PHYS_TEST=1` loads Level1 then
  lua `loadNextLevel` for 2-4, steps 300 frames, and checks contacts plus
  dynamic Y still near the ground.   `AB_PLAY_TEST=1` taps PLAY, level 1,
  TUTORIAL_OK, and a slingshot. Host also writes a raw 240x432 `panel-*.ppm`
  matching a nano framebuffer grab. Lua `cameraFunction` owns the play view;
  only leftover iPad tables (sx>1, 1024-tall) are rescaled to 480x320. RECIPE 7
  fitted an AABB around every body (`camfit x=-432..2006 scale=0.20 top=-1420`)
  which is the slingshot-to-castle span, not exploding physics (`rest=1`);
  lua then logged `Camera target object outside level limits`. That zoom left
  black/white voids on device and a second `drawGame` painted the world over
  the pause page. RECIPE 8 (device test 4) dropped the AABB and the extra
  `drawGame`. RECIPE 9 (device test 5) dropped the whole-frame pan/zoom from
  `62b2fb5` (`fe_off` / `view_zoom` moved HUD and pause with a drag) and
  passes the second finger through so native pinch works. RECIPE 10 made the
  host fail the way the iPod did: after Level 1, the next level kept iPad
  camera tables (sx about 2.25, screenHeight 1024), so draw showed one
  slingshot filling the panel and white void around it. `ab_load_lua_level`
  hid that by forcing `defaultCamera` after a rescale the game's own load
  did not keep. RECIPE 11 rescales `castleCameraData` / `birdCameraData` to
  480x320 before `cameraFunction` draws, clamps pinch to the play scale so
  zoom goes both ways, and writes `LEVEL` plus a `sync.txt` last-step
  marker from `ab_watch_level` (the device never calls the host-only
  load helpers, which is why cef3060's LEVEL lines never reached
  `/Apps/Data/AngryBirds/log.txt`). RECIPE 12 copies the bird camera onto
  `castleCameraData` so the castle intro cannot keep a wood-block close-up
  on L2+, and `AB_DEVICE_TEST` uses the map-select load (`ab_goto_level`)
  rather than `ab_load_lua_level`. A full `lua_gc` runs before each map load
  and on every `levelName` change (in-game `loadNextLevel` too) so dead level
  tables do not fill the 2 MB heap by Level 9. Shape logs are
  type-aware (circle vs polygon); the old dump read every fixture as a
  polygon, so circles looked like a nonsense vertex count and NaN size.
  Guest malloc payload is 8-aligned (4-byte header, heap base 4-mod-8);
  LDRD/VLDR.64 targets are checked. Host `AB_DEVICE_TEST=1` plays L1 then L2,
  L3, L1 again, then chapter 1, with a pinch out then in on every level, a
  one-finger pan while zoomed out, the device-like OS heap (3.86 MB free,
  2 MB then 1 MB then 512 KB largest; allocs that do not fit return NULL),
  512 texture cap, and `docs/log.txt` plus `sync.txt`. RECIPE 13 converts
  IPA PNGs to PVR RGBA4444 (`.pvr` + `.dat` patch) at install so
  SPLASHES/MENU never new[] 400-515 KB RGBA8 decode buffers (c9880d1 ran
  out of the leftover 512 KB and uploaded uninitialised GPU memory: rainbow
  menu and blocks, fail counter 0). Convert is one POT 16-bit store from
  `plat_os_alloc`; a failed convert counts as fail and draws a solid
  fallback. Host `AB_REPRO_UNINIT` plus original IPA PNGs still shows the
  rainbow and the lying fail=0. Two-finger pinch latches down to
  `minWorldScale` (whole level, or bird/castle span if lua min is stuck at
  play scale); one-finger drag pans in game x. RECIPE 14 models both OS
  heaps in `AB_DEVICE_TEST` (`AB_OS_HEAP=low` is c9880d1 with Bluetooth and
  music: 3.86 MB free, 2 MB largest; `AB_OS_HEAP=clean` is the Fruit Ninja
  clean boot: 6.01 MB free, 4 MB largest). Chapter 1 must pass on clean and
  on low once textures are 4444. If start or a texture convert cannot fit,
  the app draws the same red NOT ENOUGH MEMORY / CHECK LOG.TXT screen as
  PvZ and never uploads uninitialised GPU memory. `AB_OS_HEAP=busy` is
  below both (1 MB largest) and is the host red-screen check.
  RECIPE 15 writes the latched pinch/pan look-at into `birdCameraData` /
  `castleCameraData` px/py (lua `cameraFunction` draws from those, not from
  `setTopLeft` alone) and keeps the play-camera ground at the bottom of the
  zoomed view so L2/L3 pinch-out is not a slingshot in empty sky. Host
  `AB_DEVICE_TEST` then fails if a captured panel has no ground pixels, if
  vis-object projections land on sky/void, if a pan dump matches the
  pinch-out dump, or if a white/cyan void column is wider than 12 pixels.
  RECIPE 16 latched pinch/pan into `setTopLeft` but lua `cameraFunction`
  still drew the slingshot view, so L2/L3 pan dumps were grass and sky
  while the checks used the latched camera. RECIPE 17 writes the swipe
  camera into GameLua+368 before `drawGame`, puts it back after lua
  `setTopLeft`, and discards sprite-sheet padding (a=0) so L2's 194x196
  static block is not a white rectangle over the pigs. A one-finger swipe
  on empty space pans at play scale (the slingshot still grabs the bird)
  until the camera hits the level's right limit, so every pig is on screen
  and drawn. Pan and pinch are not forwarded as LBUTTON. Full pinch-out
  uses lua `minWorldScale` (or a fit of slingshot-to-pigs) and fills cyan
  past the sky tiles with sky colour instead of raising the zoom floor.
  The host fails unless each live pig projects inside the panel with lime
  pig pixels at that spot (grass is not a pig), pinch-out also shows the
  slingshot birds, and a structure block is drawn. L1, L2, L3, L8 and L21
  run pinch then pan then pinch-in.
  RECIPE 18 patches the Ka3D 2D transform Image::draw actually reads
  (the pointer getRenderState2D returns, not only EGL_Context+0x10C) so a
  swipe moves the pigs and castle. World sprite x,y are baked to
  (world-left, world-top) and leftover xf+48/xf+32 translations from the
  opening camera are cleared, otherwise L2 pigs at world ~1100 clip past
  NDC +1. MaskedImage FBO compositing keeps its own xf. The right pan
  limit is the live pig/block span (no 430 look-at cap). Pinch-out frames
  slingshot and every pig; cyan past the sky tiles is filled with the
  theme sky colour. Image::draw's clip AABB is skipped so rotated ice
  still emits. Checks look for real pig-green pixels at each projected
  body (no debug tint).
  RECIPE 19 forces Image::draw's remaining clip (`n_!=v_` to L_b1870)
  on so rotated ice still emits, and drops tall pause-page screenshot
  quads plus huge unsifted THEME_GROUND blits so pinch-out is not a
  pebble rectangle over the pigs.
  RECIPE 20 skips every Image::draw clip AABB branch (the generated
  `(n_!=v_)` / `(n_==v_)` forms RECIPE 19 missed), saves/restores
  MaskedImage's 2D xf around nested draws, never bakes already-scaled
  pixels (that warped theme-2/3 backgrounds at min zoom), and counts
  live pig bodies by piglette restitution (0.05) so StoneBlock8 / helmet
  extras are not pigs. GESTURE/DEVICE pan and pinch-out every chapter-1
  level that exists.
  RECIPE 21 drops leftover instance rows after lua "Removing physics
  objects" (bodies gone, block definitions still in the table). Empty
  slots zero leftover mask (+36), angle (+76) and overflow chains so
  L3 after L2 does not blit MaskedImage's empty FBO (brown diamonds).
  Hashtable::clear / D1+C1 is not used: before destroy it leaks Box2D
  bodies, during createBox it wipes definitions. Guest free poisons
  with 0xDB; first-fit carves the high end of a hole so lua_gc plus
  address-ordered alloc cannot hand L2 Image addresses back to L3.
  `AB_HEAP_NOREUSE` leaks the block instead. Named GL textures with no
  pixels (MaskedImage's no-op FBO) are not blitted. The MaskedImage
  compositor at GameLua+460 is D1+C1 on each load so L2's FBO 2x2 does
  not paint L3 pan ice as untextured rectangles. Binding a named GL
  texture re-enables TEXTURE_2D; glDisable(TEXTURE_2D) is ignored
  (Framebuffer bind is a no-op, so MaskedImage never restores it).
  drawGame zeros slot+36 masks and skips the compositor flush blit so
  sprites go through Sprite::draw instead of the empty FBO. Isolation
  (`AB_ISOLATE_LEVEL=N`) boots, inits lua, and loads that level directly.
  `AB_GESTURE_TEST=1`
  checks a drag never moves the composited frame, a two-finger pinch changes
  worldScale, L1 then L2 then L3 load in one session, and the L1/L2 panel
  dumps pass the same ground/void pixel checks. VCMP
  writes FPSCR only (APSR via FMSTAT), matching ARM; the old immediate `n_=`
  collapsed Box2D AABBs to a point. PLAY, TUTORIAL_OK, and a slingshot drag
  work through LBUTTON plus `mouseMove`.
- **Writes:** `Angry Birds.hbapp` (code only, about 2.3 MB; no game assets)
  and `/Apps/Data/AngryBirds` from the user's ipa. `nabase add` copies with
  `replace=True`; `nabase remove` is not needed to overwrite.

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
