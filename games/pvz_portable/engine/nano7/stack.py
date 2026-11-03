import bisect,re,sys
syms=[]
for l in open('/tmp/syms2.txt'):
    p=l.split(None,2)
    if len(p)==3: syms.append((int(p[0],16),p[2].strip()))
syms.sort(); A=[a for a,_ in syms]
line=[l for l in open(sys.argv[1]) if 'SEGV' in l][0]
ws=re.findall(r'[0-9a-f]+', line.split('stack:')[1]); eip=re.search(r'eip ([0-9a-f]+)',line).group(1)
print(line[:60])
for x in [eip]+ws[:int(sys.argv[2]) if len(sys.argv)>2 else 16]:
    v=int(x,16); i=bisect.bisect_right(A,v)-1; print(x, syms[i][1][:120])
