#!/usr/bin/env python3
"""nano7 PvZ dry-run file audit: reads the dry run's log (stderr) and the device data folder it ran on, and fails if
the run compiled a definition from XML, or looked for a file that the device folder does not have and that is not a
known, harmless probe. Image look-ups try several names and extensions before the one that exists (for a reanim
image: REANIM_X and particles/REANIM_X, each as .n7t .png .jpg .gif .tga, before reanim/x.n7t); those misses are fine
as long as some folder has the image. With the hashed d/XX layout the names come from files.lst, and sounds/ is
not shipped at all (silent build, every sound call is a no-op). Usage: audit_files.py LOG DATA_DIR"""
import os, re, sys
OK = [r'userdata(/.*)?', r'main\.pak', r'volume\.txt', r'properties/(default|pvz-portable|layout)\.xml',
      r'sounds/.*']   # first run / not shipped on purpose (no sounds)
IMG = re.compile(r'\.(n7t(\.\d+)?|png|jpe?g|gif|tga)$', re.I)
def main(log, data):
    data = os.path.normpath(data); have = set()
    lst = os.path.join(data, 'files.lst')
    if os.path.exists(lst) and open(lst).readline().startswith('#n7x'):
        names = [l.split('\t')[0].rsplit('/', 1)[-1] for l in open(lst) if l.strip() and not l.startswith('#')]
    else:
        names = [f for _, _, fs in os.walk(data) for f in fs]
    for f in names:
        k = IMG.sub('', f.lower())
        if k != f.lower(): have.add(k)
    bad, probes, total = [], 0, 0
    for line in open(log, errors='replace'):
        line = line.rstrip('\n')
        if line.startswith('compile ') or 'refused to compile' in line:
            bad.append('compiled at run time: ' + line); continue
        if not line.startswith('nano7 miss '): continue
        total += 1
        p = os.path.normpath(line[11:])
        rel = (p[len(data) + 1:] if p.startswith(data + '/') else p).lower()
        if any(re.fullmatch(r, rel) for r in OK): continue
        if IMG.search(rel):
            k = IMG.sub('', os.path.basename(rel)).rstrip('_')
            if k in have or (k.startswith('reanim_') and k[7:] in have): probes += 1; continue
        bad.append('missing: ' + rel)
    print(f'audit: {total} misses, {probes} image probes with the image found elsewhere, {len(bad)} problems')
    for b in sorted(set(bad))[:200]: print('  ' + b)
    return 1 if bad else 0
if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
