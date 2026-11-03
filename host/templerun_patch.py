"""nano7kit's build-time changes to the fetched nano-templerun tree.

olievans123/nano-templerun has no OSS licence, so nano7kit never ships his
files. These are nano7kit's own edits, applied to the pinned checkout on the
user's Mac at build time, each anchored on a short line of the pinned commit.
A missing anchor stops the build (the pin moved) instead of building blind.

Why: on a slot B Home with 11 apps and a music library the OS heap reads
about 6.6 MB free / 4.0 MB largest. After the 1.38 MB image, arena_init()'s
"heap free minus a 3.5 MB GL reserve" clamp gave the arena about 1.7 MB,
but engine + models need about 3.1 MB, so init failed under the IMANGI
splash. The edits:
  1. arena_init asks for the full arena first (one block, still the aux
     block the resident frees on exit), stepping down 64 KB at a time to a
     3.125 MB floor, and only then falls back to the old clamp;
  2. the per-frame triangle budget follows live heap free (about 3 KB per
     triangle, from the port's own notes: 1,550 triangles rebooted at
     3.87 MB free), 400 to 1,300, so a smaller heap draws less scenery
     instead of rebooting the driver;
  3. (data, see shrink_textures) the three 1024 scenery sheets ship at 256.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path
from typing import Dict, List

from .compat import Refused, root

MARK = "/* nano7kit memory edits */"

ARENA_FLOOR = 0x320000
BYTES_PER_TRIANGLE = 3000
BUDGET_MIN = 400
BUDGET_MAX = 1300
# budget.txt in /Apps/Data/TempleRun may lower bytes per triangle (more
# scenery, less reboot margin). 1,550 triangles rebooted at 3.87 MB free
# (about 2,500 bytes each), so never below 2,600.
BPT_MIN = 2600
BPT_MAX = 6000

PATCHED_FILES = ("port/nano/nano_alloc.c", "port/nano/nano_platform.c", "port/nano/Makefile")
# Pre-flight: before the engine allocates anything, refuse to start (and say so on the
# panel) when the arena block or the GL driver's share is not there, e.g. with music playing.
TEXTURE_BYTES = 942_080                 # templerun.TEXTURE_BYTES
PREFLIGHT_TRIANGLES = 200
NEED_LARGEST = ARENA_FLOOR + 0x10000
NEED_FREE = ARENA_FLOOR + TEXTURE_BYTES + PREFLIGHT_TRIANGLES * BYTES_PER_TRIANGLE
NOTICE_FILES = ("notice.c", "notice.h", "gl_left.c", "gl_left.h")   # from games/fruitninja/nano, nano7kit's own

_ARENA_OLD = """    if (freeb > GL_RESERVE + PORT_ARENA_MIN && freeb - GL_RESERVE < size)
        size = (freeb - GL_RESERVE) & ~0xFFFFu;
    else if (freeb <= GL_RESERVE + PORT_ARENA_MIN)
        size = PORT_ARENA_MIN;
    while (!(sArena = hb_os_alloc(size)) && size > 0x100000u) size -= 0x80000u;
"""

_ARENA_DECL_OLD = """static void arena_init(void)
{
"""

_ARENA_DECL_NEW = """uint32_t n7_arena_size, n7_arena_free, n7_arena_largest, n7_arena_cap; """ + MARK + """
static void arena_init(void)
{
"""

_ARENA_NEW = """    """ + MARK + """
    /* Whole arena first: engine + models need about 3.1 MB whatever the GL
     * reserve says, and the triangle budget (nano_platform.c) now follows
     * heap free, so the driver gets what is left. Still one block. */
    /* when Temple Run has refused to start, only a small arena for the log */
    if (n7_arena_cap && n7_arena_cap < size) size = n7_arena_cap;
    sArena = hb_os_alloc(size);
    while (!sArena && size > 0x%XU) { size -= 0x10000u; sArena = hb_os_alloc(size); }
    if (!sArena && !n7_arena_cap) {
        size = PORT_ARENA_SIZE;
        if (freeb > GL_RESERVE + PORT_ARENA_MIN && freeb - GL_RESERVE < size)
            size = (freeb - GL_RESERVE) & ~0xFFFFu;
        else if (freeb <= GL_RESERVE + PORT_ARENA_MIN)
            size = PORT_ARENA_MIN;
        while (!(sArena = hb_os_alloc(size)) && size > 0x100000u) size -= 0x80000u;
    }
    n7_arena_size = sArena ? size : 0u;
    n7_arena_free = freeb;
    n7_arena_largest = hb_os_heap_largest();
""" % ARENA_FLOOR

_BUDGET_OLD = """    keep_awake();
    uint64_t now=plat_time_us();
"""

_BUDGET_NEW = """    keep_awake();
    {   """ + MARK + """
        /* The driver's per-frame memory comes out of the OS heap: give it
         * about %d bytes a triangle of what is free, %d..%d. */
        static uint32_t n7_tick, n7_bpt; static int n7_last;
        if(!n7_bpt){
            /* optional budget.txt (bytes per triangle, %d..%d) to trade
             * reboot margin for draw distance; absent or bad: %d. */
            uint32_t sz=0,v=0; char *t=plat_read_file("budget.txt",&sz,1);
            if(t){ for(uint32_t i=0;i<sz&&t[i]>='0'&&t[i]<='9'&&v<100000u;i++) v=v*10u+(uint32_t)(t[i]-'0'); free(t); }
            n7_bpt=(v>=%du&&v<=%du)?v:%du;
            plat_log("nano7kit bytes per triangle %%u%%s",n7_bpt,v&&n7_bpt==v?" (budget.txt)":"");
        }
        if((n7_tick++ & 15u)==0u){
            uint32_t f=hb_os_heap_free(); int b=(int)(f/n7_bpt);
            if(b<%d)b=%d;
            if(b>%d)b=%d;
            fe_option_budget=b;
            if(b!=n7_last&&(n7_last==0||b<n7_last-100||b>n7_last+100)){
                plat_log("nano7kit budget %%d triangles (heap free %%u)",b,f);n7_last=b;
            }
        }
    }
    uint64_t now=plat_time_us();
""" % (BYTES_PER_TRIANGLE, BUDGET_MIN, BUDGET_MAX,
       BPT_MIN, BPT_MAX, BYTES_PER_TRIANGLE, BPT_MIN, BPT_MAX, BYTES_PER_TRIANGLE,
       BUDGET_MIN, BUDGET_MIN, BUDGET_MAX, BUDGET_MAX)

_LOG_ARENA = (
    'plat_log("nano7kit arena %u bytes (heap free %u, largest %u before it; floor %u)",'
    "(unsigned)n7_arena_size,(unsigned)n7_arena_free,(unsigned)n7_arena_largest,"
    + "0x%Xu);" % ARENA_FLOOR
)

_FAIL_OLD = """            plat_log("initialization failed");"""
_FAIL_NEW = """            { extern uint32_t n7_arena_size,n7_arena_free,n7_arena_largest; """ + _LOG_ARENA + """ } """ + MARK + """
            plat_log("initialization failed");"""

_OK_OLD = """        initialized=1;
"""
_OK_NEW = """        initialized=1;
        { extern uint32_t n7_arena_size,n7_arena_free,n7_arena_largest; """ + _LOG_ARENA + """ } """ + MARK + """
"""

_FRAME_OLD = """    if(w<1||h<1||failed)return;
"""
_FRAME_NEW = """    if(w<1||h<1)return;
    if(failed){ if(n7_short) n7_short_notice(w,h); return; }   """ + MARK + """
"""

_PREFLIGHT_OLD = """        port_crumb("heap0",hb_os_heap_free(),0);
"""
_PREFLIGHT_NEW = """        {   """ + MARK + """
            /* pre-flight, arena-aware: the OS allocator reboots the iPod when it runs dry,
             * so the arena (made on the first malloc, above) must be whole and the GL driver's
             * share still free before the engine starts */
            extern uint32_t n7_arena_size;
            n7_step("pre-flight");
            uint32_t f=hb_os_heap_free(),l=hb_os_heap_largest();
            int short_=n7_arena_size ? (n7_arena_size<0x%Xu||f<%uu) : (l<0x%Xu||f<%uu);
            if(short_){
                plat_log("nano7kit not enough memory: heap free %%u largest %%u with arena %%u, needs arena %%u and %%u free after it (music playing?); not starting",
                         (unsigned)f,(unsigned)l,(unsigned)n7_arena_size,0x%Xu,%uu);
                /* the screen shows what Probe would have read before Temple Run's arena */
                n7_refuse(f+n7_arena_size,n7_arena_size>l?n7_arena_size:l);flush_log();failed=1;fatal_armed=0;return;
            }
        }
        port_crumb("heap0",hb_os_heap_free(),0);
""" % (ARENA_FLOOR, NEED_FREE - ARENA_FLOOR, NEED_LARGEST, NEED_FREE, ARENA_FLOOR, NEED_FREE - ARENA_FLOOR)

_SHORT_DECL_OLD = """static int file_count,initialized,failed;
"""
_SHORT_DECL_NEW = """static int file_count,initialized,failed;
#include "notice.h"   """ + MARK + """
static int n7_short;static char n7_note[2][40];static const char *n7_lines[7];
static void n7_mb(char *o,uint32_t n){snprintf(o,12,"%%u.%%uM",(unsigned)(n>>20),(unsigned)(((n&0xFFFFFu)*10u)>>20));}
static void n7_short_set(uint32_t f,uint32_t l){
    char a[12],b[12],c[12],d[12];n7_mb(a,f);n7_mb(b,l);n7_mb(c,%uu);n7_mb(d,0x%Xu);
    snprintf(n7_note[0],sizeof n7_note[0],"FREE %%s BIG %%s",a,b);
    snprintf(n7_note[1],sizeof n7_note[1],"NEEDS %%s BIG %%s",c,d);
    n7_lines[0]="TEMPLE RUN";n7_lines[1]="NOT ENOUGH MEMORY";n7_lines[2]="";
    n7_lines[3]=n7_note[0];n7_lines[4]=n7_note[1];n7_lines[5]="PAUSE MUSIC, QUIT,";n7_lines[6]="THEN OPEN IT AGAIN";
    n7_short=1;
}
static void n7_short_notice(int w,int h){notice_draw(w,h,n7_lines,7);}
/* free and largest after every start-up step, so a refusal can be traced in log.txt */
static void n7_step(const char *what){
    extern uint32_t n7_arena_size;
    plat_log("nano7kit step %%s: heap free %%u largest %%u, arena %%u",what,
             (unsigned)hb_os_heap_free(),(unsigned)hb_os_heap_largest(),(unsigned)n7_arena_size);
}
/* refusing: keep the arena the log flush will create small (it is freed on the next launch) */
static void n7_refuse(uint32_t f,uint32_t l){
    extern uint32_t n7_arena_size,n7_arena_cap;
    if(!n7_arena_size) n7_arena_cap=0x10000u;
    n7_short_set(f,l);
}
""" % (NEED_FREE, NEED_LARGEST)

_MAKE_OLD = """PLATFORM := nano_platform nano_alloc nano_libc nano_memory
"""
_MAKE_NEW = """PLATFORM := nano_platform nano_alloc nano_libc nano_memory notice gl_left
"""

# textures left in the resident's shared GL context by the previous port (Fruit Ninja's
# included) are deleted before Temple Run creates its own, and Temple Run records the names it
# was given so the next port deletes them (games/fruitninja/nano/gl_left.c).
_GLL_INC_OLD = """#include "gl.h"
"""
_GLL_INC_NEW = """#include "gl.h"
#include "gl_left.h"   """ + MARK + """
"""
_EARLY_OLD = """            {   /* the previous run's trail, if it ended in a reboot: kept under a new name each time */
"""
_EARLY_NEW = ("""            {   """ + MARK + """
                /* before anything is allocated. Upstream makes its whole arena (one OS
                 * block of about 3.2 MB) on the first malloc, the breadcrumb ring just below;
                 * an earlier check ran after that and measured Temple Run's own arena. */
                uint32_t f0=hb_os_heap_free(),l0=hb_os_heap_largest();
                gll_init(DATA_DIR "/gl_names.txt");
                uint32_t swept=gll_sweep();
                plat_log("nano7kit: %%u old texture names deleted; heap free %%u largest %%u before, %%u and %%u after",
                         (unsigned)swept,(unsigned)f0,(unsigned)l0,(unsigned)hb_os_heap_free(),(unsigned)hb_os_heap_largest());
                uint32_t f=hb_os_heap_free(),l=hb_os_heap_largest();
                if(l<0x%Xu||f<%uu){
                    plat_log("nano7kit not enough memory before start: heap free %%u largest %%u, needs %%u and %%u (music playing?); not starting",
                             (unsigned)f,(unsigned)l,%uu,0x%Xu);
                    n7_refuse(f,l);flush_log();failed=1;fatal_armed=0;return;
                }
            }
            {   /* the previous run's trail, if it ended in a reboot: kept under a new name each time */
""" % (NEED_LARGEST, NEED_FREE, NEED_FREE, NEED_LARGEST))
_GLL_SWEEP_OLD = """            port_crumb("init",0,0);
"""
_GLL_SWEEP_NEW = """            n7_step("previous trail");   """ + MARK + """
            port_crumb("init",0,0);
            n7_step("first allocation (breadcrumb ring, so the arena)");
"""
_MANIFEST_OLD = """            if(manifest()){plat_log("files.lst is missing");flush_log();failed=1;fatal_armed=0;return;}
"""
_MANIFEST_NEW = _MANIFEST_OLD + """            n7_step("files.lst");   """ + MARK + """
"""
_SPLASH_OLD = """            game_splash(w,h,splash_frames>0);
"""
_SPLASH_NEW = _SPLASH_OLD + """            if(!splash_frames) n7_step("splash texture");   """ + MARK + """
"""
_GLL_NOTE_OLD = """    glGenTextures(1,&t);if(!t){plat_log("texture %s: no texture name",name);return 0;}
"""
_GLL_NOTE_NEW = """    glGenTextures(1,&t);if(!t){plat_log("texture %s: no texture name",name);return 0;}
    gll_note(t);   """ + MARK + """
"""
_GLL_SAVE_OLD = """static void flush_log(void) {
"""
_GLL_SAVE_NEW = """static void flush_log(void) {
    gll_save();   """ + MARK + """
"""

EDITS: Dict[str, List[tuple]] = {
    "port/nano/nano_alloc.c": [(_ARENA_DECL_OLD, _ARENA_DECL_NEW), (_ARENA_OLD, _ARENA_NEW)],
    "port/nano/nano_platform.c": [
        (_FAIL_OLD, _FAIL_NEW),
        (_OK_OLD, _OK_NEW),
        (_BUDGET_OLD, _BUDGET_NEW),
        (_SHORT_DECL_OLD, _SHORT_DECL_NEW),
        (_FRAME_OLD, _FRAME_NEW),
        (_PREFLIGHT_OLD, _PREFLIGHT_NEW),
        (_GLL_INC_OLD, _GLL_INC_NEW),
        (_EARLY_OLD, _EARLY_NEW),
        (_GLL_SWEEP_OLD, _GLL_SWEEP_NEW),
        (_MANIFEST_OLD, _MANIFEST_NEW),
        (_SPLASH_OLD, _SPLASH_NEW),
        (_GLL_NOTE_OLD, _GLL_NOTE_NEW),
        (_GLL_SAVE_OLD, _GLL_SAVE_NEW),
    ],
    "port/nano/Makefile": [(_MAKE_OLD, _MAKE_NEW)],
}


def apply(repo: Path, *, restore: bool = True) -> List[str]:
    """Restore the pinned files, then apply nano7kit's edits. Returns the edited paths."""
    repo = Path(repo)
    if restore and (repo / ".git").exists():
        subprocess.check_call(["git", "-C", str(repo), "checkout", "--quiet", "--", *PATCHED_FILES])
    done = []
    for rel, edits in EDITS.items():
        path = repo / rel
        text = path.read_text(encoding="utf-8")
        if MARK in text:
            raise Refused(f"{rel} is already patched and could not be restored; delete {repo} and re-run")
        for old, new in edits:
            if text.count(old) != 1:
                raise Refused(
                    f"nano-templerun changed: nano7kit's edit no longer matches {rel}. "
                    "Nothing was staged."
                )
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
        done.append(rel)
    src = root() / "games" / "fruitninja" / "nano"
    for name in NOTICE_FILES:
        shutil.copyfile(src / name, repo / "port" / "nano" / name)
    return done


# ------------------------------------------------------------------ data

PVR_HEADER = 52
PVR_TAG = 0x21525650  # 'PVR!'
SCENERY_MAX = 256
SHRINK_ABOVE = 512  # only the 1024 scenery sheets; splash and UI sheets stay as they are


def _level_bytes(w: int, h: int, bpp: int) -> int:
    if bpp == 4:
        return max(w, 8) * max(h, 8) // 2
    return max(w, 16) * max(h, 8) // 4


def shrink_pvr(data: bytes, limit: int = SCENERY_MAX) -> bytes:
    """Drop top mip levels of a mipmapped PVRTC v2 file until it is <= limit wide."""
    if len(data) < PVR_HEADER:
        return data
    (hsize, h, w, mips, flags, dlen, bpp) = struct.unpack_from("<7I", data, 0)
    tag = struct.unpack_from("<I", data, 44)[0]
    if hsize != PVR_HEADER or tag != PVR_TAG or bpp not in (2, 4):
        return data
    if mips == 0 or max(w, h) <= max(limit, SHRINK_ABOVE):
        return data
    off = PVR_HEADER
    while max(w, h) > limit and mips > 0:
        off += _level_bytes(w, h, bpp)
        w, h, mips = max(w // 2, 1), max(h // 2, 1), mips - 1
    body = data[off : PVR_HEADER + dlen]
    head = bytearray(data[:PVR_HEADER])
    struct.pack_into("<4I", head, 4, h, w, mips, flags)
    struct.pack_into("<I", head, 20, len(body))
    return bytes(head) + body


def shrink_textures(nano_data: Path, limit: int = SCENERY_MAX) -> List[str]:
    """Shrink every mipmapped .pvr above `limit` in place and rewrite files.lst sizes."""
    nano_data = Path(nano_data)
    changed = []
    for p in sorted(nano_data.glob("*.pvr")):
        raw = p.read_bytes()
        out = shrink_pvr(raw, limit)
        if out != raw:
            p.write_bytes(out)
            changed.append(p.name)
    lst = nano_data / "files.lst"
    if changed and lst.is_file():
        lines = []
        for line in lst.read_text(encoding="utf-8").splitlines():
            name = line.rsplit(" ", 1)[0]
            f = nano_data / name
            lines.append(f"{name} {f.stat().st_size}" if f.is_file() else line)
        lst.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    return changed
