# Shared helpers. Sourced, not run. Portable (macOS + Linux).
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD="$ROOT/build"
UNTETHER="$ROOT/untether"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing tool: $1 (run ./start deps)"; }

# Prefer GNU sha256sum; fall back to shasum -a 256 (macOS) or python.
hash_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -- "$1" | awk '{print $1}'
  else
    python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
  fi
}
expect_hash() {
  local expected=$1 file=$2 actual
  actual=$(hash_of "$file")
  [[ $actual == "$expected" ]] || die "hash mismatch for $file
  expected $expected
  actual   $actual"
}

# shellcheck disable=SC1091
. "$ROOT/untether/scripts/disk-size.sh"
