/* nano7kit measurement: wraps malloc/free/realloc/calloc etc. and keeps per-pointer size + tag. */
#include "memtrack.h"
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
__thread int mt_tag = MT_OTHER;
void *__real_malloc(size_t); void __real_free(void*); void *__real_realloc(void*, size_t);
void *__real_calloc(size_t, size_t); void *__real_aligned_alloc(size_t, size_t);
int __real_posix_memalign(void**, size_t, size_t); void *__real_memalign(size_t, size_t);
#define CAP (1u<<22)
typedef struct { uintptr_t p; uint32_t sz; int32_t tag; uint32_t site[4]; } Ent;
static Ent *tab;
static volatile int lk;
static size_t atpeak[MT_NTAGS], atpeak_last; static int atpeak_lasttag;
int mt_phase, mt_atpeak_phase;
static size_t live[MT_NTAGS], peak_all, peak_np, peaktag[MT_NTAGS], cur_all, cur_np, cur_model, peak_model, cur_nano, peak_nano, nalloc, big_np, big_px;
static void lock(void){ while (__sync_lock_test_and_set(&lk,1)) ; }
static void unlock(void){ __sync_lock_release(&lk); }
static inline uint32_t h(uintptr_t p){ uint64_t x=p; x^=x>>17; x*=0xed5ad4bbu; x^=x>>11; return (uint32_t)x & (CAP-1); }
static inline size_t model(size_t s){ return ((s+7)&~(size_t)7)+8; }
extern char __executable_start, etext;
static size_t trapmin = (size_t)-1; static int trapinit; static FILE *trapf;
static void trap(size_t s){
  uintptr_t *fp = (uintptr_t*)__builtin_frame_address(0); int n=0;
  fprintf(trapf, "ALLOC %zu tag %d:", s, mt_tag);
  uintptr_t lo=(uintptr_t)fp, hi=lo+(1u<<20);
  while (fp && (uintptr_t)fp>=lo && (uintptr_t)fp<hi && n<16){ uintptr_t ra=fp[1]; if (ra>(uintptr_t)&__executable_start && ra<(uintptr_t)&etext) fprintf(trapf," %lx",(unsigned long)ra); n++; fp=(uintptr_t*)fp[0]; }
  fprintf(trapf,"\n");
}
static void add(void *p, size_t s){
  if (!p) return;
  if (!trapinit){ trapinit=1; const char *e=getenv("NANO7_TRAPMIN"); if(e){ trapmin=strtoul(e,0,10); trapf=fopen(getenv("NANO7_TRAPOUT")?getenv("NANO7_TRAPOUT"):"trap.txt","w"); } }
  { static int px=-1; if (px<0) px = getenv("NANO7_TRAPPX")?1:0;
    if (s>=trapmin && trapf && (px ? mt_tag==MT_PIXELS : mt_tag!=MT_PIXELS)) trap(s); }
  lock();
  if (!tab){ tab = (Ent*)__real_calloc(CAP, sizeof(Ent)); }
  uint32_t i=h((uintptr_t)p);
  while (tab[i].p && tab[i].p!=1) i=(i+1)&(CAP-1);
  int t=mt_tag; tab[i].p=(uintptr_t)p; tab[i].sz=(uint32_t)s; tab[i].tag=t;
  { static int want=-1; if (want<0) want = getenv("NANO7_SITES")?1:0;
    memset(tab[i].site,0,sizeof(tab[i].site));
    if (want){ uintptr_t *fp=(uintptr_t*)__builtin_frame_address(0); uintptr_t lo=(uintptr_t)fp, hi=lo+(1u<<20); int k=0, n=0;
      while (fp && (uintptr_t)fp>=lo && (uintptr_t)fp<hi && n<12 && k<4){ uintptr_t ra=fp[1]; if (n>=2) tab[i].site[k++]=(uint32_t)ra; n++; fp=(uintptr_t*)fp[0]; } } }
  live[t]+=s; cur_all+=s; if (t==MT_DECODE){} else if (t!=MT_PIXELS){ cur_np+=s; cur_model+=model(s); cur_nano+=model(s); if (s>big_np) big_np=s; } else { cur_nano+=s*4/25; if (s>big_px) big_px=s; }
  if (cur_nano>peak_nano){ peak_nano=cur_nano; for(int k=0;k<MT_NTAGS;k++) atpeak[k]=live[k]; atpeak_last=s; atpeak_lasttag=t; mt_atpeak_phase=mt_phase; }
  if (cur_all>peak_all) peak_all=cur_all; if (cur_np>peak_np) peak_np=cur_np; if (cur_model>peak_model) peak_model=cur_model;
  if (live[t]>peaktag[t]) peaktag[t]=live[t];
  nalloc++;
  unlock();
}
static void del(void *p){
  if (!p || !tab) return;
  lock();
  uint32_t i=h((uintptr_t)p);
  while (tab[i].p){ if (tab[i].p==(uintptr_t)p){ size_t s=tab[i].sz; int t=tab[i].tag; live[t]-=s; cur_all-=s; if (t==MT_DECODE){} else if(t!=MT_PIXELS){cur_np-=s; cur_model-=model(s); cur_nano-=model(s);} else cur_nano-=s*4/25; tab[i].p=1; nalloc--; break; } i=(i+1)&(CAP-1); }
  unlock();
}
void *__wrap_malloc(size_t s){ void *p=__real_malloc(s); add(p,s); return p; }
void *__wrap_calloc(size_t n, size_t s){ void *p=__real_calloc(n,s); add(p,n*s); return p; }
void __wrap_free(void *p){ del(p); __real_free(p); }
void *__wrap_realloc(void *o, size_t s){ del(o); void *p=__real_realloc(o,s); if(p) add(p,s); return p; }
void *__wrap_aligned_alloc(size_t a, size_t s){ void *p=__real_aligned_alloc(a,s); add(p,s); return p; }
void *__wrap_memalign(size_t a, size_t s){ void *p=__real_memalign(a,s); add(p,s); return p; }
int __wrap_posix_memalign(void **r, size_t a, size_t s){ int e=__real_posix_memalign(r,a,s); if(!e) add(*r,s); return e; }
void mt_reset_peak(void){ lock(); peak_all=cur_all; peak_np=cur_np; peak_model=cur_model; peak_nano=cur_nano; for(int i=0;i<MT_NTAGS;i++) peaktag[i]=live[i]; big_np=0; big_px=0; unlock(); }
size_t mt_live(int t){ return t<0?cur_all:live[t]; }
size_t mt_live_nonpixel(void){ return cur_np; }
size_t mt_peak(void){ return peak_all; }
size_t mt_peak_nonpixel(void){ return peak_np; }
size_t mt_peak_tag(int t){ return peaktag[t]; }
size_t mt_model_live(void){ return cur_model; }
size_t mt_peak_model(void){ return peak_model; }
size_t mt_count(void){ return nalloc; }
size_t mt_biggest_nonpixel(void){ return big_np; }
size_t mt_biggest_pixel(void){ return big_px; }
static const char *names[MT_NTAGS]={"other","pixels","reanim","particle+trail","fonts","pools","file buffers","strings","resource tables","sound","board","decode"};
const char* mt_tag_name(int t){ return names[t]; }
void mt_dump_big(const char* path, size_t minsize){
  FILE *f=fopen(path,"w"); if(!f) return; lock();
  for (uint32_t i=0;i<CAP;i++) if (tab[i].p>1 && tab[i].sz>=minsize) fprintf(f,"%u %s %x %x %x %x\n",tab[i].sz,names[tab[i].tag],tab[i].site[0],tab[i].site[1],tab[i].site[2],tab[i].site[3]);
  unlock(); fclose(f);
}

size_t mt_nano_live(void){ return cur_nano; }
size_t mt_peak_nano(void){ return peak_nano; }
size_t mt_atpeak_tag(int t){ return atpeak[t]; }
size_t mt_atpeak_last(void){ return atpeak_last; }
int mt_atpeak_lasttag(void){ return atpeak_lasttag; }
