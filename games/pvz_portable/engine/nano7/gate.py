"""M1 gate: heap <= 3.5 MB and program + heap + textures + 1.25 MB GL headroom <= 6.8 MB, per level.

heap = measured nano heap peak (non-pixel blocks with +8 B/align-8 allocator model, CPU pixel buffers x0.16 for 240-high)
     + lazily reloaded image bits that outlived their texture upload (x0.16)
     + 64 KB texture upload strip buffer (device streams .n7t files)
     + decode-tagged memory still live at level end, minus the measuring tool's own texture-size table
textures = peak resident set, sized from the real repacked 16-bit .n7t files (generated textures computed at x0.4, 16-bit)
"""
import json, sys
M = 1e6; PROG = 1.13; GL = 1.25; STRIP = 0.064
def rows(f):
    for l in open(f):
        d = json.loads(l)
        if 'level' not in d: continue
        h = d['heap_peak_nano'] / M + d.get('lazy_survivor_peak', 0) * 0.16 / M + STRIP + (d['tags_live'].get('decode', 0) - d.get('bookkeeping_decode', 0)) / M
        hl = d['heap_live_nano'] / M + STRIP + (d['tags_live'].get('decode', 0) - d.get('bookkeeping_decode', 0)) / M
        tn = d.get('tex_peak_n7t', 0) / M; tm = d['tex_peak_mix'] / M
        yield d, h, hl, tn, tm, PROG + h + tn + GL, PROG + h + tm + GL
if __name__ == '__main__':
    for f in sys.argv[1:]:
        print('==', f)
        print('lvl   result heap-peak heap-live | tex(n7t 16-bit) tex(mix est) | total(16-bit) total(mix) | gate(16-bit)')
        for d, h, hl, tn, tm, tot, totm in rows(f):
            ok = 'PASS' if h <= 3.5 and tot <= 6.8 else 'FAIL'
            print('1-%-2d  %-5s  %6.2f   %6.2f   | %6.2f          %6.2f       | %6.2f        %6.2f    | %s' % (d['level'], d['result'], h, hl, tn, tm, tot, totm, ok))
