# First-party NanoApps template

A direct-framebuffer app (RAW_SURFACE, HBAppKind 1, no LVGL), like Paint and Lap.
See docs/PORTING.md.

```sh
./start sdk new NAME --title Title
./start sdk build NAME
# Disk Mode:
./start apps add Title
```

Tokens KIT_FOLDER / KIT_TITLE / KIT_BUNDLE / KIT_DATA / KIT_COLOR / KIT_GLYPH
are replaced by `./start sdk new`. Do not copy original game files into this
folder. Do not enable LVGL unless you have measured size vs the 0.5 MB launch
cap. GL (kind 2) must follow `./start port rules`.
