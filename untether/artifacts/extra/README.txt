NanoApps extras staged for ./start apps add.
Hardware-tested three (Blocks, Game Boy, Probe) stay in artifacts/device.
Extras beyond Blocks, Game Boy and Probe are tested one per reboot. Authors: see CREDITS.md.
No Nintendo ROMs. Native Mario stages via ./start apps add Mario --rom ROM.z64.
First-party Lap (RAW_SURFACE stopwatch) is staged: ./start apps add Lap.
First-party JS Hello (PocketJS 240x432 HAL, not the runtime): ./start pocketjs hello then ./start apps add 'JS Hello'.
olievans123 iOS ports need --payload and stay out of git (no OSS licence).
Built against the hb_shared SDK (no fixed live-OS addresses).
Already installed? ./start apps refresh re-copies them in one Disk Mode step.
