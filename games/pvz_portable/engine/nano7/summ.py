import json,sys
M=1e6
for f in sys.argv[1:]:
    print('==',f)
    print('lvl  peakNP  liveNP  model  reanimTag  reanimPeak pools poolsPk other fonts  biggestNP | rawR dedR q16R defs | tex n t16 p16 mix | resident t16 mix')
    for l in open(f):
        d=json.loads(l)
        if 'level' not in d:
            print('boot liveNP %.2f peakNP %.2f'%(d['heap_live_nonpixel']/M,d['heap_peak_nonpixel']/M), {k:round(v/M,3) for k,v in d['tags_live'].items()}); continue
        tl,tp,r,t,tr=d['tags_live'],d['tags_peak'],d['reanim'],d['tex_drawn'],d['tex_resident']
        print('1-%-2d %s %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f | %.2f %.2f %.2f %d | %d %.2f %.2f %.2f | %.2f %.2f'%(d['level'],d['result'][0],d['heap_peak_nonpixel']/M,d['heap_live_nonpixel']/M,d['heap_peak_model']/M,tl['reanim']/M,tp['reanim']/M,tl['pools']/M,tp['pools']/M,tl['other']/M,tl['fonts']/M,d['biggest_nonpixel']/M,r['raw']/M,r['dedupe']/M,r['q16']/M,r['defs'],t['n'],t['t16']/M,t['p16']/M,t['mix']/M,tr['t16']/M,tr['mix']/M))
