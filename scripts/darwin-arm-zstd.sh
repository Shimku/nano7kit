#!/usr/bin/env bash
# macOS only: make the Arm GNU toolchain's compiler run when Homebrew's libzstd is
# missing.
#
# The darwin-x86_64 Arm GNU Toolchain (14.2.rel1) links cc1, cc1plus and lto1 (and
# sometimes ld) against /usr/local/opt/zstd/lib/libzstd.1.dylib, a Homebrew path.
# On a Mac without Homebrew, every compile then dies in dyld with
# "Library not loaded: .../libzstd.1.dylib".
#
#   scripts/darwin-arm-zstd.sh --check          exit 0 if arm-none-eabi-gcc runs
#   scripts/darwin-arm-zstd.sh --check-or-fix   check, and fix if needed (below)
#   scripts/darwin-arm-zstd.sh --fix [--zstd /path/libzstd.1.dylib]
#
# The fix only touches the toolchain folder (your own files, never /usr or the
# system): it copies a libzstd.1.dylib into <toolchain>/lib, keeps a backup of
# every binary it edits under <toolchain>/zstd-fix-backup, rewrites the missing
# path to @loader_path so the toolchain carries its own copy, and re-signs the
# edited files ad hoc. Undo: copy the backups back. Nothing touches the iPod.
# Under sudo it runs the edits as you ($SUDO_USER), so ownership stays yours.
set -euo pipefail

MODE=${1:---check}
shift || true
ZSTD_SRC=${NANO7_ZSTD_DYLIB:-}
while [[ $# -gt 0 ]]; do
  case $1 in
    --zstd) ZSTD_SRC=${2:-}; shift 2;;
    *) echo "usage: $0 [--check|--check-or-fix|--fix] [--zstd PATH]" >&2; exit 2;;
  esac
done

[[ $(uname -s) == Darwin ]] || exit 0

GCC=${NANO7_ARM_GCC:-$(command -v arm-none-eabi-gcc || true)}
[[ -n $GCC ]] || { echo "error: arm-none-eabi-gcc is not on PATH (source your env.sh)" >&2; exit 1; }

probe() { # 0 = compiler runs; prints the failure otherwise
  local tmp out
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/n7zstd.XXXXXX")
  printf 'int nano7_probe(int a) { return a * 3; }\n' >"$tmp/t.c"
  if out=$("$GCC" -mcpu=cortex-a8 -mthumb -c "$tmp/t.c" -o "$tmp/t.o" 2>&1); then
    rm -rf "$tmp"; return 0
  fi
  rm -rf "$tmp"
  printf '%s\n' "$out"
  return 1
}

if [[ $MODE == --check || $MODE == --check-or-fix ]]; then
  if msg=$(probe); then exit 0; fi
  if ! grep -q 'libzstd' <<<"$msg"; then
    printf 'error: arm-none-eabi-gcc cannot compile a test file:\n%s\n' "$msg" >&2
    exit 1
  fi
  if [[ $MODE == --check ]]; then
    printf 'arm-none-eabi-gcc needs libzstd:\n%s\nFix: %s --fix\n' "$msg" "$0" >&2
    exit 1
  fi
fi

# ---- fix ----
TC=$(cd "$(dirname "$GCC")/.." && pwd -P)
if [[ $(id -u) -eq 0 && -n ${SUDO_USER:-} ]]; then
  # Re-run as the toolchain's owner, so nothing in it becomes root-owned.
  exec sudo -H -u "$SUDO_USER" env NANO7_ARM_GCC="$GCC" PATH="$PATH" \
    bash "$0" --fix ${ZSTD_SRC:+--zstd "$ZSTD_SRC"}
fi
[[ -w $TC ]] || { echo "error: $TC is not writable by $(id -un); not fixing" >&2; exit 1; }
command -v install_name_tool >/dev/null || { echo "error: install_name_tool missing (xcode-select --install)" >&2; exit 1; }

if [[ -z $ZSTD_SRC ]]; then
  for cand in \
      "$TC/lib/libzstd.1.dylib" \
      "${NANO7KIT_INTEL_PREFIX:-/nonexistent}/lib/libzstd.1.dylib" \
      "$(cd "$(dirname "$0")/.." && pwd)/build/macos-intel/lib/libzstd.1.dylib" \
      /opt/homebrew/lib/libzstd.1.dylib /usr/local/lib/libzstd.1.dylib \
      /opt/local/lib/libzstd.1.dylib \
      "${CONDA_PREFIX:-/nonexistent}/lib/libzstd.1.dylib" \
      "$HOME/nano7/env/lib/libzstd.1.dylib" \
      "$HOME/opt/anaconda3/lib/libzstd.1.dylib" "$HOME/anaconda3/lib/libzstd.1.dylib" \
      "$HOME/miniconda3/lib/libzstd.1.dylib" "$HOME/miniforge3/lib/libzstd.1.dylib" \
      /opt/anaconda3/lib/libzstd.1.dylib; do
    if [[ -f $cand ]]; then ZSTD_SRC=$cand; break; fi
  done
fi
if [[ -z $ZSTD_SRC || ! -f $ZSTD_SRC ]]; then
  cat >&2 <<MSG
error: no libzstd.1.dylib found on this Mac.
Get one without Homebrew: ./scripts/macos-intel-bootstrap.sh builds a pinned
libzstd into build/macos-intel/lib. Then run: $0 --fix
(or pass --zstd /path/to/libzstd.1.dylib)
MSG
  exit 1
fi

file "$ZSTD_SRC" | grep -q x86_64 \
  || { echo "error: $ZSTD_SRC is not an x86_64 dylib" >&2; exit 1; }

mkdir -p "$TC/lib" "$TC/zstd-fix-backup"
if [[ $ZSTD_SRC != "$TC/lib/libzstd.1.dylib" ]]; then
  cp "$ZSTD_SRC" "$TC/lib/libzstd.1.dylib"
  chmod u+w "$TC/lib/libzstd.1.dylib"
  install_name_tool -id @rpath/libzstd.1.dylib "$TC/lib/libzstd.1.dylib" 2>/dev/null || true
  codesign -s - -f "$TC/lib/libzstd.1.dylib" >/dev/null 2>&1 || true
fi

fixed=0
while IFS= read -r -d '' bin; do
  file "$bin" | grep -q 'Mach-O' || continue
  while IFS= read -r dep; do
    [[ $dep == *libzstd*.dylib ]] || continue
    [[ $dep == @* ]] && continue
    [[ -f $dep ]] && continue
    rel=$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], os.path.dirname(sys.argv[2])))' \
          "$TC/lib/libzstd.1.dylib" "$bin")
    bak="$TC/zstd-fix-backup/${bin#"$TC"/}"
    mkdir -p "$(dirname "$bak")"
    [[ -f $bak ]] || cp -p "$bin" "$bak"
    install_name_tool -change "$dep" "@loader_path/$rel" "$bin"
    codesign -s - -f "$bin" >/dev/null 2>&1 || true
    echo "  fixed $(basename "$bin"): $dep -> @loader_path/$rel"
    fixed=$((fixed + 1))
  done < <(otool -L "$bin" 2>/dev/null | awk 'NR > 1 {print $1}')
done < <(find "$TC/bin" "$TC/libexec" -type f -perm -u+x -print0)

echo "libzstd fix: $fixed binary(ies) updated; backups in $TC/zstd-fix-backup"
if msg=$(probe); then
  echo "arm-none-eabi-gcc runs."
else
  printf 'error: arm-none-eabi-gcc still fails:\n%s\n' "$msg" >&2
  exit 1
fi
