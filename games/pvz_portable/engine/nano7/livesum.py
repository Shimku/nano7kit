import bisect, collections, sys
S=[]
for l in open(sys.argv[2]):
    a,t,n=l.rstrip('\n').split(' ',2); S.append((int(a,16),n))
S.sort(); A=[x[0] for x in S]
def f(a):
    if a==0: return ''
    i=bisect.bisect_right(A,a)-1; n=S[i][1]; return n.split('(')[0][-55:]
SKIP=('wrap_','operator new','__libcpp','allocator','make_unique','basic_string','vector','__split_buffer','__tree','__hash','construct','unique_ptr','map<','__emplace','insert','emplace','operator[]','__construct','__append','resize','reserve','push_back','assign','__init')
agg=collections.defaultdict(lambda:[0,0]); tot=collections.Counter()
for l in open(sys.argv[1]):
    p=l.split(); sz=int(p[0]); tag='_'.join(p[1:-4]); fr=[f(int(x,16)) for x in p[-4:]]
    fr=[x for x in fr if x and not any(k in x for k in SKIP)][:2]
    k=tag+' | '+' <- '.join(fr); agg[k][0]+=sz; agg[k][1]+=1; tot[tag]+=sz
print(dict(tot))
for k,v in sorted(agg.items(), key=lambda kv:-kv[1][0])[:int(sys.argv[3]) if len(sys.argv)>3 else 50]:
    if not k.startswith('pixels'): print('%9d %6d %s'%(v[0],v[1],k))
