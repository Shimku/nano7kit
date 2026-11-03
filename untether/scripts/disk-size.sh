# Sourced. Accept the hardware-tested USB size, or the IPodRepartition
# size when the MBR is type 0x0c / start LBA 64 (MKN52 4K FAT32 map).
TESTED_DISK_SIZE=15798411264
IPODREPARTITION_DISK_SIZE=15806623744

mbr_ipodrepartition_4k() {
  local path=$1
  python3 -c '
import struct, sys
path = sys.argv[1]
with open(path, "rb") as f:
    data = f.read(512)
if len(data) < 512 or data[510] != 0x55 or data[511] != 0xAA:
    sys.exit(1)
typ = data[0x1BE + 4]
start = struct.unpack_from("<I", data, 0x1BE + 8)[0]
sys.exit(0 if typ == 0x0C and start == 64 else 1)
' "$path"
}

accept_nano_size() {
  local size=$1
  local path=$2
  if [[ $size == "$TESTED_DISK_SIZE" ]]; then
    return 0
  fi
  if [[ $size == "$IPODREPARTITION_DISK_SIZE" ]]; then
    if [[ -z ${path:-} || ! -e $path ]]; then
      printf 'REFUSED: size %s needs an MBR path to prove 0x0c start 64\n' "$size" >&2
      return 1
    fi
    if ! mbr_ipodrepartition_4k "$path"; then
      printf 'REFUSED: size %s is the IPodRepartition map but MBR is not type 0x0c start 64\n' "$size" >&2
      return 1
    fi
    return 0
  fi
  printf 'REFUSED: size %s is not %s or %s\n' "$size" "$TESTED_DISK_SIZE" "$IPODREPARTITION_DISK_SIZE" >&2
  return 1
}
