# Gravity Defied device build: engine C++ in ARM state (Thumb VFP traps on this firmware)
# into libgd_nano.a. gd_plat.c / gd_nano.c / notice.c are compiled by the app Makefile.
import glob
import os
import subprocess
import sys
import concurrent.futures as cf

R = os.path.dirname(os.path.abspath(__file__))
TC = os.environ.get("NANO7_TC", "")
NK = os.environ.get("NANO7KIT", os.path.abspath(os.path.join(R, "..", "..", "..")))
skip = {
    "src/main.cpp",
    "src/nano/gd_plat.c",
    "src/nano/malloc-2.8.6.c",
    "src/utils/EmbedFileStream.cpp",
    "src/utils/FileStream.cpp",
}
srcs = []
for pat in (
    "src/*.cpp",
    "src/lcdui/*.cpp",
    "src/rms/*.cpp",
    "src/utils/*.cpp",
    "src/nano/*.cpp",
    "src/nano/*.c",
):
    for s in glob.glob(os.path.join(R, pat)):
        rel = os.path.relpath(s, R)
        if rel in skip or "/tests/" in rel:
            continue
        srcs.append(s)
srcs = sorted(set(srcs))
inc = [
    "-I" + os.path.join(R, "src"),
    "-I" + os.path.join(R, "src", "nano"),
    "-I" + os.path.join(R, "src", "lcdui"),
]
arch = ["-marm", "-mcpu=cortex-a5", "-mfloat-abi=softfp", "-mfpu=vfpv4-d16", "-mword-relocations"]
common = arch + [
    "-Os", "-ffp-contract=off", "-ffunction-sections", "-fdata-sections", "-fno-common",
    "-fno-strict-aliasing", "-fwrapv", "-fno-short-enums", "-fno-exceptions", "-fno-rtti",
    "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-std=gnu++17",
]
out = os.path.join(R, "obj_arm")
os.makedirs(out, exist_ok=True)


def comp(s):
    rel = os.path.relpath(s, R)
    o = os.path.join(out, rel.replace("/", "_") + ".o")
    if s.endswith(".c"):
        cmd = [TC + "arm-none-eabi-gcc"] + arch + [
            "-Os", "-ffunction-sections", "-fdata-sections", "-fno-common", "-std=gnu11",
            "-fno-exceptions", "-fno-short-enums",
        ] + inc + ["-c", s, "-o", o]
    else:
        cmd = [TC + "arm-none-eabi-g++"] + common + inc + ["-c", s, "-o", o]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return s, r.returncode, r.stderr, o


objs = []
bad = 0
with cf.ThreadPoolExecutor(os.cpu_count() or 2) as ex:
    for s, rc, err, o in ex.map(comp, srcs):
        if rc:
            bad += 1
            print("FAIL", os.path.relpath(s, R))
            print("\n".join(err.splitlines()[:20]))
        else:
            objs.append(o)
print("sources", len(srcs), "failed", bad)
if bad:
    sys.exit(1)
lib = os.path.join(out, "libgd_nano.a")
if os.path.exists(lib):
    os.remove(lib)
r = subprocess.run([TC + "arm-none-eabi-ar", "crs", lib] + objs, capture_output=True, text=True)
print(r.stderr[-2000:] if r.stderr else "")
print("lib", lib, os.path.getsize(lib) if os.path.exists(lib) else 0)
sys.exit(r.returncode)
