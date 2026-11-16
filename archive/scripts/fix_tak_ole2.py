"""
fix_tak_ole2.py
===============
Patches corrupted OptiTrack .tak files where the OLE2 compound file directory
entry claims a larger size than the actual sector chain contains.

The left-leg .tak files have Track 0.trk directory entries that claim ~9.2 MB
but only ~7.6 MB of sectors are actually present, causing Motive to report the
file as corrupt. This script:
  1. Backs up the original file
  2. Parses the raw OLE2 structure to find directory entries
  3. Patches any stream whose claimed size exceeds the readable sector chain
  4. Also patches the Track 0.trk internal header size fields (offsets 12-23)

Usage:
    python fix_tak_ole2.py <path_to_tak>
    python fix_tak_ole2.py <path_to_tak> --dry-run
"""

import struct
import shutil
import sys
import os

# Force stdout to UTF-8 so non-ASCII stream names print cleanly
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ENDOFCHAIN  = 0xFFFFFFFE
FREESECT    = 0xFFFFFFFF
FATSECT     = 0xFFFFFFFD
DIFSECT     = 0xFFFFFFFC

OLE2_MAGIC  = b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1'


def read_sector(f, sec_num, sector_size):
    f.seek(512 + sec_num * sector_size)
    return f.read(sector_size)


def build_fat(f, header, sector_size):
    """Build the full FAT (sector → next_sector map) from the compound file."""
    num_fat = struct.unpack_from('<I', header, 44)[0]
    fat = {}

    # Sector IDs stored in the header DIFAT array (up to 109 entries at bytes 76-508)
    difat_in_header = [struct.unpack_from('<I', header, 76 + i * 4)[0] for i in range(109)]

    fat_sectors = [s for s in difat_in_header if s < DIFSECT]

    # Follow DIFAT chain if > 109 FAT sectors
    first_difat = struct.unpack_from('<I', header, 68)[0]
    difat_sec = first_difat
    while difat_sec < DIFSECT:
        data = read_sector(f, difat_sec, sector_size)
        n = sector_size // 4 - 1  # last entry = next DIFAT sector
        for i in range(n):
            s = struct.unpack_from('<I', data, i * 4)[0]
            if s < DIFSECT:
                fat_sectors.append(s)
        difat_sec = struct.unpack_from('<I', data, sector_size - 4)[0]

    for fat_sec in fat_sectors:
        data = read_sector(f, fat_sec, sector_size)
        n = sector_size // 4
        base = fat_sec * n  # NOT correct — FAT entries index by absolute sector
        # Actually FAT is a flat array indexed by sector number
        # fat_sectors[0] covers sectors 0..n-1, fat_sectors[1] covers n..2n-1, etc.
        # But we can't know the offset without knowing which FAT sector covers which range.
        # Instead store (fat_sector_idx → data) and compute sector coverage
        pass

    # Rebuild properly: each FAT sector covers consecutive blocks of n entries
    # But we don't know which block each FAT sector covers without a mapping.
    # The DIFAT gives FAT sector locations in ORDER, so fat_sectors[0] covers
    # absolute sectors 0..(n-1), fat_sectors[1] covers n..(2n-1), etc.
    n = sector_size // 4
    fat = {}
    for idx, fat_sec in enumerate(fat_sectors):
        data = read_sector(f, fat_sec, sector_size)
        for i in range(n):
            abs_sec = idx * n + i
            fat[abs_sec] = struct.unpack_from('<I', data, i * 4)[0]
    return fat


def chain_length(fat, start_sector, sector_size):
    """Return number of sectors in the chain starting at start_sector."""
    if start_sector >= DIFSECT:
        return 0
    count = 0
    sec = start_sector
    seen = set()
    while sec < DIFSECT and sec != FREESECT:
        if sec in seen:
            break  # cycle
        seen.add(sec)
        count += 1
        sec = fat.get(sec, ENDOFCHAIN)
    return count


def read_directory_entries(f, fat, header, sector_size):
    """Yield (file_offset, raw_128_bytes) for every used directory entry."""
    first_dir = struct.unpack_from('<I', header, 48)[0]
    n_per_sec = sector_size // 128
    sec = first_dir
    seen = set()
    while sec < DIFSECT and sec not in seen:
        seen.add(sec)
        base_offset = 512 + sec * sector_size
        f.seek(base_offset)
        data = f.read(sector_size)
        for i in range(n_per_sec):
            entry = data[i * 128:(i + 1) * 128]
            obj_type = entry[66]
            if obj_type in (1, 2, 5):  # storage, stream, root
                yield base_offset + i * 128, entry
        sec = fat.get(sec, ENDOFCHAIN)


def parse_entry(raw):
    name_len  = struct.unpack_from('<H', raw, 64)[0]
    name      = raw[:name_len].decode('utf-16-le', errors='replace').rstrip('\x00')
    obj_type  = raw[66]
    start_sec = struct.unpack_from('<I', raw, 116)[0]
    size_lo   = struct.unpack_from('<I', raw, 120)[0]
    size_hi   = struct.unpack_from('<I', raw, 124)[0]
    size      = size_lo | (size_hi << 32)
    return name, obj_type, start_sec, size


def fix_tak(tak_path, dry_run=False):
    backup = tak_path + ".bak"
    if not os.path.exists(backup):
        print("Backing up to: %s" % backup)
        if not dry_run:
            shutil.copy2(tak_path, backup)
    else:
        print("Backup already exists: %s" % backup)

    with open(tak_path, 'r+b' if not dry_run else 'rb') as f:
        header = f.read(512)
        if header[:8] != OLE2_MAGIC:
            print("ERROR: Not a valid OLE2 compound file")
            return False

        sector_size_exp = struct.unpack_from('<H', header, 30)[0]
        sector_size     = 1 << sector_size_exp
        print("Sector size: %d bytes" % sector_size)

        fat = build_fat(f, header, sector_size)
        print("FAT entries: %d" % len(fat))

        patches_applied = 0
        for offset, raw in read_directory_entries(f, fat, header, sector_size):
            name, obj_type, start_sec, claimed_size = parse_entry(raw)
            if obj_type != 2:   # only streams
                continue
            if claimed_size == 0:
                print("  Stream '%s': 0 bytes (skip)" % name)
                continue

            # Compute actual readable size from sector chain
            n_sectors = chain_length(fat, start_sec, sector_size)
            actual_max = n_sectors * sector_size
            # Actual size = min(claimed, sector chain capacity)
            # If claimed > chain capacity → corruption
            if claimed_size > actual_max:
                print("  Stream '%s': claimed=%d  chain_max=%d  -> MISMATCH" % (
                    name, claimed_size, actual_max))

                # The true data size is what the chain can hold; we don't know
                # the exact byte count within the last sector, so we use chain_max
                # as a safe upper bound. A better estimate: read actual bytes.
                # We'll use the chain_max as the corrected size.
                corrected = actual_max
                print("    Patching size: %d → %d" % (claimed_size, corrected))
                if not dry_run:
                    f.seek(offset + 120)
                    f.write(struct.pack('<I', corrected & 0xFFFFFFFF))
                    f.write(struct.pack('<I', (corrected >> 32) & 0xFFFFFFFF))
                patches_applied += 1
            else:
                print("  Stream '%s': %d bytes (OK)" % (name, claimed_size))

        if patches_applied == 0:
            print("\nNo size mismatches found — file may have a different issue.")
        else:
            print("\nApplied %d patch(es)." % patches_applied)

        # ── Patch Track 0.trk internal header (offsets 12-23 are often zero) ──
        # Re-find Track 0 stream to patch its internal header
        print("\nPatching Track 0.trk internal size fields (offsets 12-23)...")
        for offset, raw in read_directory_entries(f, fat, header, sector_size):
            name, obj_type, start_sec, size_field = parse_entry(raw)
            if name == "Track 0" and obj_type == 2:
                if start_sec >= DIFSECT:
                    print("  Track 0 has invalid start sector — skip internal patch")
                    break
                # Read the actual stream size we just patched (or from raw entry)
                actual_size = struct.unpack_from('<I', raw, 120)[0]  # may be patched by now
                # The track header is the first 28 bytes of the stream data
                # Offsets 12-15 and 20-23 should store the data size
                track_hdr_offset = 512 + start_sec * sector_size
                f.seek(track_hdr_offset)
                track_hdr = bytearray(f.read(28))
                print("  Track 0 header before patch: %s" % track_hdr.hex())

                # For right-leg: bytes 12-15 = stream_size - 180, bytes 20-23 = stream_size - 54
                # We'll write the actual_size (or a reasonable approximation)
                # Use actual_size directly in both fields as a safe default
                if struct.unpack_from('<I', track_hdr, 12)[0] == 0:
                    struct.pack_into('<I', track_hdr, 12, actual_size)
                    print("  Patched bytes 12-15 → %d" % actual_size)
                if struct.unpack_from('<I', track_hdr, 20)[0] == 0:
                    struct.pack_into('<I', track_hdr, 20, actual_size)
                    print("  Patched bytes 20-23 → %d" % actual_size)

                if not dry_run:
                    f.seek(track_hdr_offset)
                    f.write(bytes(track_hdr))
                break

    print("\nDone. %s" % ("(dry-run, no changes written)" if dry_run else "File patched in-place."))
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fix_tak_ole2.py <tak_file> [--dry-run]")
        sys.exit(1)

    path = sys.argv[1]
    dry = "--dry-run" in sys.argv

    if not os.path.isfile(path):
        print("ERROR: file not found:", path)
        sys.exit(1)

    fix_tak(path, dry_run=dry)
