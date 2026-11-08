# Credits

nano7kit is mostly integration work on top of these projects.

## Fetched at a pinned commit when you install or build, not included here

| Project | Author | Used for | Licence |
|---|---|---|---|
| [nano7-untethered](https://github.com/olievans123/nano7-untethered) | olievans123 | Untethered loader, Blocks, Game Boy, Probe, Mario 64 and Mario Kart 64 ports | none stated |
| [nano-templerun](https://github.com/olievans123/nano-templerun), [nano-flappybird](https://github.com/olievans123/nano-flappybird), [nano-doodlejump](https://github.com/olievans123/nano-doodlejump) | olievans123 | Temple Run, Flappy Bird and Doodle Jump ports | none stated |
| ipod_sun_untethered (bundled in nano7-untethered), [ipod_sun](https://github.com/CUB3D/ipod_sun) | nfzerox, CUB3D | Firmware exploit and builder | GPL-3.0 |
| [wInd3x](https://github.com/freemyipod/wInd3x) | q3k and freemyipod | Recovery mode and flashing (patched for the Mid-2015 model) | GPL-2.0 |
| [PocketJS](https://github.com/pocket-nexus/pocketjs) | pocket-nexus (Yifeng "Evan" Wang) | JavaScript and TypeScript runtime, pinned at `6ca9f914` | MIT ([LICENSES/PocketJS-MIT.txt](LICENSES/PocketJS-MIT.txt)) |
| [QuickJS](https://bellard.org/quickjs/) | Fabrice Bellard and Charlie Gordon | JavaScript engine in JS QuickJS | MIT ([LICENSES/QuickJS-MIT.txt](LICENSES/QuickJS-MIT.txt)) |
| dlmalloc 2.8.6 | Doug Lea | allocator fetched at build for JS QuickJS and PvZ Portable | public domain / CC0 ([LICENSES/dlmalloc-CC0.txt](LICENSES/dlmalloc-CC0.txt)) |
| [PvZ-Portable](https://github.com/wszqkzqk/PvZ-Portable) | wszqkzqk | Plants vs. Zombies engine | LGPL-3.0 |
| [ipod_theme](https://github.com/nfzerox/ipod_theme) | nfzerox | Optional theming (`./start theme`) | GPL-3.0 |
| [LVGL](https://github.com/lvgl/lvgl) | LVGL contributors | Widgets for the NanoApps apps | MIT |
| [NanoApps 1.0 release](https://github.com/nfzerox/NanoApps/releases) | nfzerox | `NanoApps-2015-Model.ipsw` and `NanoApps-disk.zip`, checked by SHA-256, for the NanoApps base and `nabase flash`. Its firmware includes ninjaprawn's fix for restarts during music syncs | MIT (firmware inside is Apple's) |

The Mario ports build on jnmartin84's Dreamcast ports and the n64decomp decompilations.
Credits for those are in nano7-untethered.

## Included here

| Project | Author | Used for | Licence |
|---|---|---|---|
| [NanoApps 1.0](https://github.com/nfzerox/NanoApps) | nfzerox | App framework, SDK and launcher. `nanoapps/system/silver_resident_na` is a fork of its launcher | MIT |
| NanoApps compatibility fixes ([NanoBtFix](https://github.com/Merquice/NanoBtFix), [nano7-a1717-dock](https://github.com/Raphmatt/nano7-a1717-dock)) | Merquice, Raphmatt | AirPods H2 and Lightning Dock fixes inside the launcher | as upstream |
| [PvZ-Portable](https://github.com/wszqkzqk/PvZ-Portable) changes | wszqkzqk, nano7kit | `games/pvz_portable` | LGPL-3.0 |
| [Inter](https://rsms.me/inter/) | Rasmus Andersson | Font in the PocketJS Fonts sample | OFL-1.1 ([LICENSES/Inter-OFL-1.1.txt](LICENSES/Inter-OFL-1.1.txt); also `untether/artifacts/extra/Data/PocketJS/fonts/LICENSE.txt`) |
| [Font Awesome Free](https://fontawesome.com) | Fonticons, Inc. | Home icons drawn from Font Awesome 7 Free Solid-900.otf | SIL OFL-1.1 (font), CC BY 4.0 (SVG/JS icons), MIT (code); [LICENSES/Font-Awesome-Free.txt](LICENSES/Font-Awesome-Free.txt) |
| Motion Lab studies | yui540 | Motion Lab in JS QuickJS (credit shown on screen) | as upstream |

## nano7kit's own work

- the installer, menu and hash checks
- the large-app launcher changes (a largest-block check and chunked reads in the
  patched SDK, separate screen handlers per app type, unloading before the catalog
  is read on refresh, and only re-reading the catalog after USB marked the disk
  dirty) and `.nanoapp` packaging for Paint and Free RAM
- the PocketJS nano 7G target, JS QuickJS, JS Hero, JS Samples and Free RAM
- the PvZ Portable port, the Fruit Ninja recompile, and the Angry Birds Android
  `.so` loader (the approach follows olievans123's Temple Run port; none of its
  code is used)
- memory changes to olievans123's Temple Run port, applied at build time
- FAT32 conversion on a Mac, Disk Mode music sync and library restore
- the NanoApps 1.0 base install (`host/nabase.py`) and `nabase flash` (`host/nabase_fw.py`)
