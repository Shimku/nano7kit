#!/usr/bin/env bash
# Build Mario Kart 64 and/or Super Mario 64 for the nano from your own ROMs.
#
#   scripts/build-games.sh --mk64 /path/to/mario-kart-64-us.z64 --sm64 /path/to/super-mario-64-us.z64
#
# Either game can be left out. Nothing is written to any device. The result is a folder,
# build/games-stage/Apps, laid out exactly as it goes onto the nano.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

MK64_ROM= SM64_ROM=
while [[ $# -gt 0 ]]; do
  case $1 in
    --mk64) MK64_ROM=${2:-}; shift 2;;
    --sm64) SM64_ROM=${2:-}; shift 2;;
    *) die "usage: $0 [--mk64 ROM.z64] [--sm64 ROM.z64]";;
  esac
done
[[ -n $MK64_ROM || -n $SM64_ROM ]] || die "usage: $0 [--mk64 ROM.z64] [--sm64 ROM.z64]"

MK64_MD5=3a67d9986f54eb282924fca4cd5f6dff   # Mario Kart 64 (USA)
SM64_SHA1=9bef1128717f958171a4afac3ed78ee2bb4e86ce   # Super Mario 64 (USA)

for tool in git make gcc g++ python3 arm-none-eabi-gcc; do need "$tool"; done
NANOAPPS="$ROOT/nanoapps"
JOBS=$(python3 -c 'import os; print(os.cpu_count() or 4)')
mkdir -p "$BUILD"
file_md5() { python3 -c 'import hashlib,pathlib,sys; print(hashlib.md5(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$1"; }
file_sha1() { python3 -c 'import hashlib,pathlib,sys; print(hashlib.sha1(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$1"; }

# macOS host fixes (Apple clang + the Arm GNU toolchain), so nothing is manual:
#  - armips (a host tool in both game trees) needs C++11 or later; Apple clang 14
#    defaults to C++98. CXXFLAGS reaches the tools sub-make through the environment.
#  - Apple's `cpp` is a clang driver wrapper that treats a non-.c input (e.g.
#    levels/level_rules.mk) as a linker input. Force C preprocessing explicitly.
#  - The darwin-x86_64 Arm toolchain links Homebrew's libzstd; see darwin-arm-zstd.sh.
GAME_MAKE_VARS=()
if [[ $(uname -s) == Darwin ]]; then
  export CXXFLAGS="${CXXFLAGS:+$CXXFLAGS }-std=c++17"
  GAME_MAKE_VARS+=("CPP=${GAME_CPP:-clang -E -P -x c -Wno-trigraphs}")
  bash "$ROOT/scripts/darwin-arm-zstd.sh" --check-or-fix \
    || die "arm-none-eabi-gcc cannot run on this Mac; see the message above"
fi

# Builds are stamped with the port patch and the SDK they were made against, so a
# toolkit update never mixes old objects with new SDK headers.
port_stamp() { # name
  { cat "$ROOT/games/$1/UPSTREAM" "$ROOT/games/$1/nano-port.patch"; } | python3 -c \
    'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}
sdk_stamp() {
  python3 - "$NANOAPPS/sdk" "$ROOT/nanoapps/tools/mkrelocapp.py" <<'PY'
import hashlib, pathlib, sys
h = hashlib.sha256()
sdk = pathlib.Path(sys.argv[1])
for p in sorted(sdk.rglob("*")):
    if p.is_file() and p.suffix in (".c", ".h", ".mk", ".ld", ".S"):
        h.update(p.relative_to(sdk).as_posix().encode() + b"\0" + p.read_bytes())
h.update(pathlib.Path(sys.argv[2]).read_bytes())
print(h.hexdigest())
PY
}

# Fetch an upstream port at its pinned commit and apply the nano port on top.
fetch_port() { # name
  local name=$1 dir="$BUILD/$1" url commit want
  url=$(sed -n 's/^url=//p' "$ROOT/games/$name/UPSTREAM")
  commit=$(sed -n 's/^commit=//p' "$ROOT/games/$name/UPSTREAM")
  want=$(port_stamp "$name")
  if [[ -f $dir/.nano-port-applied && $(cat "$dir/.nano-port-applied") != "$want" ]]; then
    # An older toolkit's tree (or a failed run): keep it aside, start clean.
    echo "==> $name: port tree is from an older toolkit; moving it to $dir.old.$$"
    mv "$dir" "$dir.old.$$"
  fi
  if [[ ! -f $dir/.nano-port-applied ]]; then
    [[ ! -e $dir ]] || die "$dir exists but is incomplete; move it away and run again"
    echo "==> $name: fetching $url at ${commit:0:8}"
    git clone --quiet "$url" "$dir"
    git -C "$dir" checkout --quiet --detach "$commit"
    echo "==> $name: applying the nano port"
    git -C "$dir" apply --whitespace=nowarn "$ROOT/games/$name/nano-port.patch"
    printf '%s\n' "$want" >"$dir/.nano-port-applied"
  fi
}

# Drop compiled game objects made against a different SDK (headers such as
# hb_heap.h / hb_shared.h are compiled into the game library).
sdk_fresh() { # port-dir build-subdir
  local dir=$1 out=$2 want
  want=$(sdk_stamp)
  if [[ -e $dir/$out && ( ! -f $dir/.nano-sdk-stamp || $(cat "$dir/.nano-sdk-stamp") != "$want" ) ]]; then
    echo "==> $(basename "$dir"): SDK changed since the last build; rebuilding its objects"
    rm -rf "${dir:?}/$out"
  fi
  printf '%s\n' "$want" >"$dir/.nano-sdk-stamp"
}

# The .hbapp packer (tools/mkrelocapp.py) and the icon builder need pyelftools and
# Pillow; give every step the same private venv so the system python never matters.
VENV="$BUILD/venv"
[[ -x $VENV/bin/python ]] || python3 -m venv "$VENV"
"$VENV/bin/python" -c 'import PIL, elftools' 2>/dev/null \
  || "$VENV/bin/pip" install --quiet Pillow==12.1.1 pyelftools
APP_PY=(PYTHON="$VENV/bin/python")

# sizes.txt ("<relative path> <bytes>" per line) tells each game its data file sizes.
# The game makefiles write it with GNU `stat -c %s`, which macOS stat does not have
# (every size would be blank, so the game could not load its data). Rewrite it here
# portably, on every host, with the same format.
write_sizes() { # data-dir recursive(1|0)
  python3 - "$1" "$2" <<'PY'
import pathlib, sys
d = pathlib.Path(sys.argv[1])
files = d.rglob("*.bin") if sys.argv[2] == "1" else d.glob("*.bin")
rels = sorted(p.relative_to(d).as_posix() for p in files if p.is_file())
(d / "sizes.txt").write_text("".join("%s %d\n" % (r, (d / r).stat().st_size) for r in rels))
print("sizes.txt: %d entries in %s" % (len(rels), d))
PY
}

APPS=()

if [[ -n $MK64_ROM ]]; then
  [[ -f $MK64_ROM ]] || die "no such file: $MK64_ROM"
  [[ $(file_md5 "$MK64_ROM") == "$MK64_MD5" ]] \
    || die "Mario Kart 64 ROM is not the supported USA .z64 dump (md5 $MK64_MD5)"
  need cmake
  fetch_port mk64
  D="$BUILD/mk64"
  cp "$MK64_ROM" "$D/baserom.us.z64"
  if [[ ! -x $D/tools/torch/cmake-build-release/torch ]]; then
    echo "==> mk64: building the asset extractor (Torch)"
    git -C "$D" submodule update --quiet --init tools/torch
    make -C "$D/tools/torch" type=release -j"$JOBS" >"$BUILD/mk64-torch.log" 2>&1 \
      || die "Torch build failed; see $BUILD/mk64-torch.log"
  fi
  if [[ ! -f $D/.assets-extracted ]]; then
    echo "==> mk64: extracting assets from your ROM"
    mkdir -p "$D/assets/course_metadata"
    ( cd "$D" && for step in code header "modding export"; do
        tools/torch/cmake-build-release/torch $step baserom.us.z64
      done ) >"$BUILD/mk64-assets.log" 2>&1 || die "asset extraction failed; see $BUILD/mk64-assets.log"
    touch "$D/.assets-extracted"
  fi
  echo "==> mk64: building the game (several minutes)"
  # The upstream Makefile misses a few dependencies between generated assets, so a parallel
  # build can trip over a file that isn't there yet. Go parallel first, then finish serially.
  sdk_fresh "$D" build
  MK64_MAKE=(make -C "$D" PORT=nano NANO_FLOAT=softfp NANOAPPS="$NANOAPPS" ${GAME_MAKE_VARS[@]+"${GAME_MAKE_VARS[@]}"} build/libmk64_nano.a port_data)
  "${MK64_MAKE[@]}" -j"$JOBS" -k >"$BUILD/mk64-build.log" 2>&1 \
    || "${MK64_MAKE[@]}" -j1 >>"$BUILD/mk64-build.log" 2>&1 \
    || die "Mario Kart 64 build failed; see $BUILD/mk64-build.log"
  write_sizes "$D/dc_data" 0 >>"$BUILD/mk64-build.log"
  rm -rf "$NANOAPPS/apps/mk64/build"
  make -C "$NANOAPPS/apps/mk64" NANO_FLOAT=softfp MK64_DIR="$D" "${APP_PY[@]}" >"$BUILD/mk64-app.log" 2>&1 \
    || die "Mario Kart 64 app build failed; see $BUILD/mk64-app.log"
  APPS+=(mk64)
fi

if [[ -n $SM64_ROM ]]; then
  [[ -f $SM64_ROM ]] || die "no such file: $SM64_ROM"
  [[ $(file_sha1 "$SM64_ROM") == "$SM64_SHA1" ]] \
    || die "Super Mario 64 ROM is not the supported USA .z64 dump (sha1 $SM64_SHA1)"
  fetch_port sm64
  D="$BUILD/sm64"
  cp "$SM64_ROM" "$D/baserom.us.z64"
  echo "==> sm64: building the game (several minutes)"
  sdk_fresh "$D" build
  SM64_MAKE=(make -C "$D" PORT=nano NANOAPPS="$NANOAPPS" ${GAME_MAKE_VARS[@]+"${GAME_MAKE_VARS[@]}"})
  "${SM64_MAKE[@]}" -j"$JOBS" -k >"$BUILD/sm64-build.log" 2>&1 \
    || "${SM64_MAKE[@]}" -j1 >>"$BUILD/sm64-build.log" 2>&1 \
    || die "Super Mario 64 build failed; see $BUILD/sm64-build.log"
  "${SM64_MAKE[@]}" -j"$JOBS" port_data >>"$BUILD/sm64-build.log" 2>&1 \
    || die "Super Mario 64 data build failed; see $BUILD/sm64-build.log"
  write_sizes "$D/build/us_nano/sm64data" 1 >>"$BUILD/sm64-build.log"
  rm -rf "$NANOAPPS/apps/sm64/build"
  make -C "$NANOAPPS/apps/sm64" SM64_DIR="$D" "${APP_PY[@]}" >"$BUILD/sm64-app.log" 2>&1 \
    || die "Super Mario 64 app build failed; see $BUILD/sm64-app.log"
  APPS+=(sm64)
fi

echo "==> packaging: ${APPS[*]}"
# The icon builder needs three Font Awesome files; they ship in untether/third_party.
FA="$NANOAPPS/tools/dependencies/fontawesome"
mkdir -p "$FA/metadata" "$FA/otfs"
cp "$UNTETHER/third_party/fontawesome/LICENSE.txt" "$FA/LICENSE.txt"
cp "$UNTETHER/third_party/fontawesome/metadata/icons.json" "$FA/metadata/icons.json"
cp "$UNTETHER/third_party/fontawesome/otfs/Font Awesome 7 Free-Solid-900.otf" "$FA/otfs/"

# build_apps.py always stages into /tmp/hbapps.
PATH="$VENV/bin:$PATH" python3 "$NANOAPPS/tools/build_apps.py" "${APPS[@]}" >"$BUILD/package.log" 2>&1 \
  || die "packaging failed; see $BUILD/package.log"

STAGE="$BUILD/games-stage"
[[ ! -e $STAGE ]] || mv "$STAGE" "$STAGE.old.$(date +%s)"
mkdir -p "$STAGE/Apps/Data"
cp -r /tmp/hbapps/Apps/Executables /tmp/hbapps/Apps/Icons "$STAGE/Apps/"
# Slot B reads its own pack, so the three demo apps in slot A's pack stay untouched.
cp /tmp/hbapps/Apps/AllApps.pack "$STAGE/Apps/AllApps-B.pack"
for app in "${APPS[@]}"; do
  case $app in
    mk64) mkdir -p "$STAGE/Apps/Data/MK64"; cp -r "$BUILD/mk64/dc_data" "$STAGE/Apps/Data/MK64/";;
    sm64) mkdir -p "$STAGE/Apps/Data/SM64"; cp -r "$BUILD/sm64/build/us_nano/sm64data/." "$STAGE/Apps/Data/SM64/";;
  esac
done
python3 - "$STAGE" <<'PY'
from pathlib import Path
import hashlib
import sys
stage = Path(sys.argv[1])
lines = []
for path in sorted(p for p in (stage / "Apps").rglob("*") if p.is_file()):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rel = path.relative_to(stage).as_posix()
    lines.append("%s  %s" % (digest, rel))
(stage / "manifest.sha256").write_text("\n".join(lines) + "\n")
PY
ROOT="$ROOT" STAGE="$STAGE" python3 - <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, os.environ["ROOT"])
from host.ports import stage_games_from_build
stage_games_from_build(Path(os.environ["STAGE"]))
PY

echo
echo "Games built: $STAGE/Apps ($(find "$STAGE/Apps" -type f | wc -l) files, $(du -sh "$STAGE/Apps" | cut -f1))"
echo "Next: ./start apps add Mario   (Disk Mode; already staged if SM64 was built)"
