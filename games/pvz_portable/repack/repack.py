#!/usr/bin/env python3
"""nano7 PvZ repacker (M0).

Builds nano7 asset paks from the user's OWN PvZ GOTY main.pak at build time. Nothing it writes may be
committed or redistributed: the output folder is git-ignored and derived from the user's files.

Steps
  1. read the user's main.pak
  2. run the engine in export mode to turn every animation into a compact .n7r file
  3. prune resource groups the nano build never loads (Zombatar, Store, Almanac, ...)
  4. convert every image to a 240-high (x0.4) 16-bit .n7t texture and write a size manifest
  5. write two paks:
       out/measure/main.pak  original images + .n7r + pruned resources (desktop measuring build, M1)
       out/device/main.pak   .n7t textures + .n7r + pruned resources (device build, M2)

  6. (M2) write out/nano/PvZ: the device files as a plain folder tree for /Apps/Data/PvZ on the iPod
       (the iPod can only read whole files from the start, so no pak), sound effects as small mono WAVs,
       no music, plus files.lst (name, size, sha1 prefix) so the copy can be checked.

Usage: repack.py --pak PATH/main.pak --properties PATH/properties --out OUTDIR [--engine PATH/pvz-nano7-measure]
Without --engine the animations are exported by repack/n7r_export.py (same format; used on the Mac).
"""
import argparse, os, re, subprocess, sys, shutil, struct, concurrent.futures as cf, json, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pak, n7r_export

HERE = os.path.dirname(os.path.abspath(__file__))
SCALE = 0.4
# DelayLoad_Zombatar stays while the desktop GameSelector still builds the Zombatar widget (removed with the mode in M2).
PRUNE_GROUPS = ['DelayLoad_Store', 'DelayLoad_Almanac', 'DelayLoad_ChallengeScreen',
                'DelayLoad_Zombiquarium', 'DelayLoad_MushroomGarden', 'DelayLoad_GreenHouseGarden',
                'DelayLoad_GreenHouseOverlay', 'DelayLoad_TreeOfWisdom', 'DelayLoad_Credits']
IMG_EXT = ('.png', '.jpg', '.jpeg', '.gif')

def norm(n): return n.replace('\\', '/').lower()

def export_n7r(engine, pakpath, props, work):
    run = os.path.join(work, 'export-run'); shutil.rmtree(run, ignore_errors=True); os.makedirs(os.path.join(run, 'home'))
    os.symlink(os.path.abspath(pakpath), os.path.join(run, 'main.pak'))
    os.symlink(os.path.abspath(props), os.path.join(run, 'properties'))
    outdir = os.path.join(work, 'n7r'); shutil.rmtree(outdir, ignore_errors=True); os.makedirs(outdir)
    env = dict(os.environ, HOME=os.path.join(run, 'home'), SDL_VIDEODRIVER='dummy', NANO7_LAZY_REANIM='1', NANO7_NO_MUSIC='1',
               NANO7_LAZY_IMAGES='1', NANO7_EXPORT_N7R=outdir, NANO7_OUT=os.path.join(run, 'out.jsonl'))
    r = subprocess.run([os.path.abspath(engine), '-resdir=' + run], cwd=run, env=env, capture_output=True, text=True, timeout=600)
    line = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith('N7R export:') and 'files' in l]
    errs = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith('N7R export:') and 'files' not in l]
    if not line: raise SystemExit('engine export failed:\n' + (r.stderr[-3000:]))
    files = {}
    for root, _, fs in os.walk(outdir):
        for f in fs:
            p = os.path.join(root, f); files[os.path.relpath(p, outdir).replace('/', '\\')] = open(p, 'rb').read()
    return files, line[0], errs

def export_n7r_py(files):
    out = {}
    raw = 0
    for n, b in files.items():
        l = norm(n)
        if l.startswith('reanim/') and l.endswith('.reanim'):
            out[n + '.n7r'] = n7r_export.export(b.decode('latin-1'))
            raw += len(b)
    return out, 'N7R export (python): %d files, %d bytes of XML -> %d bytes' % (len(out), raw, sum(len(v) for v in out.values())), []

SKIP_SOUNDS = {'sounds/zombiesonyourlawn.ogg'}   # the credits song (about 2.4 MB as WAV); credits are cut on the nano

def n7t_bands(b):
    """Cut one .n7t into the device's texture bands: band 0 = header + the first R rows, band i = the next R rows
    (no header). R = rows per band: the largest power of two with R x pot(tex_w) <= 32768 texels (64 KB at 16 bits),
    the same rule as Nano7DevCreateTextures in the engine's device layer."""
    import struct
    assert b[:4] == b'N7T1'
    lw, lh, tw, th, fmt, _ = struct.unpack('<6H', b[4:16])
    twp = 1
    while twp < tw: twp *= 2
    r = 1
    while r * 2 * twp <= 32768: r *= 2
    r = min(r, th)
    row = tw * 2; data = b[16:]
    assert len(data) == row * th, 'n7t size'
    parts = []
    for i in range(0, th, r):
        chunk = data[i * row:(i + r) * row]
        parts.append((b[:16] + chunk) if i == 0 else chunk)
    return parts

DXX_BUCKETS = 64   # must match nano7dev/n7index.c (low 6 bits of the hash)

def n7x_hash(rel, seed):
    """FNV-1a of the lower-case logical path, as nano7dev/n7index.c computes it (0 becomes 1)."""
    h = 2166136261 ^ seed
    for c in rel.lower().replace('\\', '/').encode('latin-1'):
        h = ((h ^ c) * 16777619) & 0xFFFFFFFF
    return h or 1

def dxx_salt(rels, tries=64):
    """The first seed for which every file and every folder has its own hash (folders share theirs)."""
    folders = {r[:i] for r in rels for i in range(len(r)) if r[i] == '/'}
    for seed in range(tries):
        hs = [n7x_hash(r, seed) for r in rels] + [n7x_hash(f, seed) for f in folders]
        if len(set(hs)) == len(hs): return seed
    sys.exit('no collision-free hash seed for the d/XX layout')

def dxx_path(rel, seed):
    h = n7x_hash(rel, seed)
    return 'd/%02X/%08X' % (h & (DXX_BUCKETS - 1), h)

def write_nano_tree(root, dev, properties, aconv, rate, work):
    """Device folder: every device-pak file except sounds, plus the user's properties folder, each stored as
    d/XX/HHHHHHHH (8.3 names in 64 small folders; the iPod's FAT reads a whole folder per lookup and reanim/ alone had
    1,977 long names, about 200 ms a lookup on hardware). files.lst maps logical paths to them by hash and carries each
    file's size and each texture's 16-byte header, so the device reads a file with one OS call. No sounds: the nano
    build is silent (no WAVs shipped, sound calls are no-ops); aconv and rate are kept for the command line only."""
    shutil.rmtree(root, ignore_errors=True); os.makedirs(root)
    files = {}
    def put(rel, data):
        files[rel.lower()] = data   # the engine's paths, in lower case (the hash ignores case anyway)
    for n, ft, b in dev:
        rel = n.replace('\\', '/')
        l = norm(rel)
        if l.startswith('sounds/') or l.endswith(('.ogg', '.wav', '.au', '.mo3')): continue
        if l.endswith('.n7t'):
            for i, part in enumerate(n7t_bands(b)): put(rel if i == 0 else '%s.%d' % (rel, i), part)
            continue
        put(rel, b)
    for r0, _, fs in os.walk(properties):
        for f in fs:
            p = os.path.join(r0, f); rel = os.path.join('properties', os.path.relpath(p, properties)).replace(os.sep, '/')
            if rel.lower() not in files:
                with open(p, 'rb') as fh: put(rel, fh.read())
    seed = dxx_salt(list(files))
    listing = []
    for rel in sorted(files):
        data = files[rel]
        p = os.path.join(root, dxx_path(rel, seed)); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as fh: fh.write(data)
        hdr = ''
        if rel.endswith('.n7t') and data[:4] == b'N7T1' and len(data) >= 16:
            hdr = '\t' + ''.join('%04x' % v for v in struct.unpack('<6H', data[4:16]))
        listing.append('%s\t%d\t%s%s' % (rel, len(data), hashlib.sha1(data).hexdigest()[:12], hdr))
    with open(os.path.join(root, 'files.lst'), 'w') as f:
        f.write('#n7x 2 salt=%d buckets=%d (logical path, size, sha1, texture header; stored as d/XX/HHHHHHHH)\n' % (seed, DXX_BUCKETS))
        for line in listing: f.write(line + '\n')
    return len(listing), sum(len(v) for v in files.values()), []

def prune_resources(xml):
    removed = []
    for g in PRUNE_GROUPS:
        pat = re.compile(r'\s*<Resources id="%s">.*?</Resources>' % re.escape(g), re.S)
        xml, n = pat.subn('', xml)
        if n: removed.append(g)
    return xml, removed

def plan_images(files, xml=''):
    """Logical images the engine would ask ImageLib for: colour file + optional '_name' / 'name_' alpha file.
    xml (resources.xml) names the alpha-only 'name_' images to keep (an earlier build dropped images/blank_.gif, IMAGE_BLANK on the
    main menu, was dropped)."""
    by = {}
    for name in files:
        l = norm(name)
        if l.endswith(IMG_EXT): by[os.path.splitext(l)[0]] = name
    plans, consumed = {}, set()
    for stem, name in by.items():
        d, b = os.path.split(stem)
        if b.startswith('_') or b.endswith('_'): continue
        a = by.get((d + '/' if d else '') + '_' + b) or by.get(stem + '_')
        plans[stem] = (name, a)
        if a: consumed.add(norm(a))
    for stem, name in by.items():   # alpha-only images ('_foo' with no 'foo'), loaded as base colour + alpha
        d, b = os.path.split(stem)
        if b.startswith('_') and norm(name) not in consumed:
            logical = (d + '/' if d else '') + b[1:]
            if logical not in plans: plans[logical] = (None, name)
    paths = {p.lower() for p in re.findall(r'path="([^"]+)"', xml)}
    for stem, name in by.items():   # alpha-only images ('foo_' with no 'foo'), when resources.xml asks for 'foo'
        d, b = os.path.split(stem)
        if b.endswith('_') and not b.startswith('_') and norm(name) not in consumed:
            logical = (d + '/' if d else '') + b[:-1]
            if logical not in plans and b[:-1] in paths: plans[logical] = (None, name)
    return plans

def convert(conv, work, files, plans, alphagrid):
    src = os.path.join(work, 'src'); dst = os.path.join(work, 'n7t'); os.makedirs(src, exist_ok=True); os.makedirs(dst, exist_ok=True)
    def path_of(name):
        p = os.path.join(src, norm(name))
        if not os.path.exists(p): os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'wb').write(files[name])
        return p
    jobs = []
    for logical, (c, a) in plans.items():
        if logical.split('/')[-1] in alphagrid: continue
        out = os.path.join(dst, logical + '.n7t'); os.makedirs(os.path.dirname(out), exist_ok=True)
        jobs.append((logical, out, path_of(c) if c else '-', path_of(a) if a else '-'))
    manifest, failed = {}, []
    def run(j):
        logical, out, c, a = j
        r = subprocess.run([conv, str(SCALE), out, c, a], capture_output=True, text=True)
        return j, r
    with cf.ThreadPoolExecutor(os.cpu_count() or 4) as ex:
        for (logical, out, c, a), r in ex.map(run, jobs):
            if r.returncode: failed.append(logical); continue
            w, h, tw, th, fmt = map(int, r.stdout.split())
            manifest[logical] = (w, h, tw, th, fmt, os.path.getsize(out) - 16)
    return manifest, failed, dst

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pak', required=True); ap.add_argument('--properties', required=True)
    ap.add_argument('--engine'); ap.add_argument('--out', required=True)
    ap.add_argument('--conv', default=os.path.join(HERE, 'n7tconv'))
    ap.add_argument('--aconv', default=os.path.join(HERE, 'n7aconv'))
    ap.add_argument('--rate', type=int, default=22050, help='sound effect sample rate for the iPod')
    ap.add_argument('--nano-only', action='store_true', help='only the iPod folder (nano/PvZ), no desktop paks')
    a = ap.parse_args()
    a.out = os.path.abspath(a.out)
    work = os.path.join(a.out, 'work'); os.makedirs(work, exist_ok=True)
    print('reading', a.pak)
    entries = pak.read_pak(a.pak)
    files = {n: b for n, ft, b in entries}; ftime = {n: ft for n, ft, b in entries}
    print('  %d files, %.1f MB' % (len(entries), sum(len(b) for b in files.values()) / 1e6))

    if a.engine:
        print('exporting animations with the engine')
        n7r, summary, errs = export_n7r(a.engine, a.pak, a.properties, work)
    else:
        print('exporting animations (python)')
        n7r, summary, errs = export_n7r_py(files)
    print('  ' + summary); [print('  ' + e) for e in errs[:10]]

    res_name = next(n for n in files if norm(n) == 'properties/resources.xml')
    xml = files[res_name].decode('latin-1')
    xml2, removed = prune_resources(xml)
    # alpha-grid images (colour + a separate alpha cel grid): the device cannot merge them at run time (no CPU pixels),
    # so the grid alpha is baked into the colour texture here. GOTY only has 1x1 grids (= a plain alpha image).
    gridpairs = {}
    for tag in re.findall(r'<Image\b[^>]*\balphagrid="[^"]+"[^>]*>', xml):
        path = re.search(r'\bpath="([^"]+)"', tag).group(1); grid = re.search(r'\balphagrid="([^"]+)"', tag).group(1)
        rows = re.search(r'\brows="(\d+)"', tag); cols = re.search(r'\bcols="(\d+)"', tag)
        if (rows and int(rows.group(1)) != 1) or (cols and int(cols.group(1)) != 1): sys.exit('alpha grid with several cels: ' + tag)
        gridpairs[norm(path).split('/')[-1]] = norm(grid).split('/')[-1]
    alphagrid = set()
    print('  pruned resource groups:', ', '.join(removed), '(%d -> %d bytes)' % (len(xml), len(xml2)))

    print('converting images to %.0f%% 16-bit textures' % (SCALE * 100))
    plans = plan_images(files, xml)
    stems = {}
    for n in files:
        l = norm(n)
        if l.endswith(IMG_EXT): stems.setdefault(os.path.splitext(l)[0].split('/')[-1], []).append(n)
    for logical in list(plans):
        b = logical.split('/')[-1]
        if b in gridpairs:
            g = [n for n in stems.get(gridpairs[b], []) if os.path.dirname(norm(n)) == os.path.dirname(logical)] or stems.get(gridpairs[b], [])
            if not g: sys.exit('alpha grid image missing for ' + logical)
            plans[logical] = (plans[logical][0], g[0])
    print('  alpha grids baked into textures:', len([l for l in plans if l.split('/')[-1] in gridpairs]))
    manifest, failed, n7tdir = convert(a.conv, work, files, plans, alphagrid)
    tot = sum(v[5] for v in manifest.values())
    print('  %d textures, %.2f MB at 16-bit (%d failed: %s)' % (len(manifest), tot / 1e6, len(failed), ', '.join(failed[:8])))
    with open(os.path.join(a.out, 'textures.tsv'), 'w') as f:
        f.write('# logical\tw\th\ttex_w\ttex_h\tfmt(1=565,2=5551,3=4444)\tbytes\n')
        for k in sorted(manifest): f.write('%s\t%s\n' % (k, '\t'.join(map(str, manifest[k]))))

    def is_music(l): return l.startswith('sounds/') and l.endswith('.mo3')
    def is_old_reanim(l): return l.startswith('compiled/reanim/') or (l.startswith('reanim/') and l.endswith('.reanim'))
    base = []
    for n, ft, b in entries:
        l = norm(n)
        if is_music(l) or is_old_reanim(l): continue
        if n == res_name: b = xml2.encode('latin-1')
        base.append((n, ft, b))
    base += [(n, 0, b) for n, b in sorted(n7r.items())]
    # device file list: textures instead of source images (alpha-grid images stay as they are)
    dev = []
    img_files = set()
    for logical, (c, al) in plans.items():
        if logical in manifest:
            for x in (c, al):
                if x: img_files.add(x)
    for n, ft, b in base:
        if n in img_files: continue
        dev.append((n, ft, b))
    for logical in sorted(manifest):
        dev.append((logical.replace('/', '\\') + '.n7t', 0, open(os.path.join(n7tdir, logical + '.n7t'), 'rb').read()))
    if not a.nano_only:   # the desktop paks (measuring and dry-run builds)
        os.makedirs(os.path.join(a.out, 'measure'), exist_ok=True)
        pak.write_pak(os.path.join(a.out, 'measure', 'main.pak'), base)
        os.makedirs(os.path.join(a.out, 'device'), exist_ok=True)
        pak.write_pak(os.path.join(a.out, 'device', 'main.pak'), dev)
        for v in ('measure', 'device'):
            p = os.path.join(a.out, v, 'main.pak'); print('wrote %s (%.1f MB, %d files)' % (p, os.path.getsize(p) / 1e6, len(base) if v == 'measure' else len(dev)))
    nroot = os.path.join(a.out, 'nano', 'PvZ')
    nfiles, nbytes, sfail = write_nano_tree(nroot, dev, a.properties, a.aconv, a.rate, work)
    print('wrote %s (%d files, %.1f MB, d/XX layout, no sounds)' % (nroot, nfiles, nbytes / 1e6))
    json.dump({'scale': SCALE, 'pruned_groups': removed, 'textures': len(manifest), 'texture_bytes': tot, 'failed': failed,
               'n7r_summary': summary, 'source_pak_sha256': hashlib.sha256(open(a.pak, 'rb').read()).hexdigest()},
              open(os.path.join(a.out, 'repack.json'), 'w'), indent=1)

if __name__ == '__main__':
    main()
