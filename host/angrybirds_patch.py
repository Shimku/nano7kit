"""nano7kit's build-time changes to the fetched nano-angrybirds tree.

olievans123/nano-angrybirds has no OSS licence, so nano7kit never ships his
files. These are nano7kit's own edits, applied to the pinned checkout on the
user's Mac at build time, each anchored on a short block of the pinned commit.
A missing anchor stops the build (the pin moved) instead of building blind.

The edits only call into nano7kit's own files, which are copied next to his:
games/angrybirds/nano/n7_ab.c (glue) and games/fruitninja/nano/notice.c and
gl_left.c (the NOT ENOUGH MEMORY screen and the shared-GL-context clean-up that
Temple Run already uses). What they do:
  1. ab_entry.c: no malloc in payload_entry (the "entry" breadcrumb made the
     game's 2 MB arena before anything else), and on the first GL frame, with
     the FPU attached, delete the textures earlier nano7kit ports left in the
     resident's shared GL context before the arena is taken;
  2. nano_platform.c: a memory pre-flight before the engine starts, a texture
     budget that follows free memory (upstream is a fixed 3 MB), a notice
     instead of a plain red or blank screen when the engine stops, and the
     game's texture names saved for the next port;
  3. gfx.c: record each texture name the game is given;
  4. Makefile: build notice.c, gl_left.c and n7_ab.c into libab_nano.a.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List

from .compat import Refused, root

MARK = "/* nano7kit angrybirds edits */"

PATCHED_FILES = (
    "port/nano/app/ab_entry.c",
    "port/nano/nano_platform.c",
    "port/nano/Makefile",
    "port/src/gfx.c",
)
# nano7kit's own files, copied into port/nano/ (source folder, name)
OWN_FILES = (
    ("games/fruitninja/nano", "notice.c"),
    ("games/fruitninja/nano", "notice.h"),
    ("games/fruitninja/nano", "gl_left.c"),
    ("games/fruitninja/nano", "gl_left.h"),
    ("games/angrybirds/nano", "n7_ab.c"),
    ("games/angrybirds/nano", "n7_ab.h"),
)

# ---- ab_entry.c (the Thumb app shell, copied to nanoapps/apps/angrybirds after patching)

_ENTRY_CRUMB_OLD = """    port_crumb("entry", hb_os_heap_free(), hb_os_heap_largest());
"""
_ENTRY_CRUMB_NEW = """    {   """ + MARK + """
        /* no malloc here: the first one makes the game's 2 MB arena, and textures an
         * earlier port left in the shared GL context are deleted first (ab_frame) */
        extern uint32_t n7_entry_free, n7_entry_largest;
        n7_entry_free = hb_os_heap_free();
        n7_entry_largest = hb_os_heap_largest();
    }
"""

_FPU_OLD = """    int r = hb_fpu_attach_current_task(&st);
"""
_FPU_NEW = """    int r = hb_fpu_attach_current_task(&st);
    if (r >= 0) { extern void n7_ab_early(void); n7_ab_early(); }   """ + MARK + """
"""

# ---- nano_platform.c (ARM state, VFP)

_INC_OLD = """#include "nano_alloc.h"
"""
_INC_NEW = """#include "nano_alloc.h"
#include "n7_ab.h"   """ + MARK + """
"""

_DEAD_OLD = """        sDead = 1;
        glViewport(0, 0, w, h);
        glDisable(GL_SCISSOR_TEST);
        glClearColor(0.6f, 0.f, 0.f, 1.f);
        glClear(GL_COLOR_BUFFER_BIT);
        return;
    }
"""
_DEAD_NEW = """        sDead = 1;
        n7_ab_dead_screen(w, h);   """ + MARK + """
        return;
    }
"""

_PREFLIGHT_OLD = """            printf("memory: arena %u, texture cache %u\\n", (unsigned)port_arena_size(), TEXTURE_BUDGET);
            load_manifest();
"""
_PREFLIGHT_NEW = """            printf("memory: arena %u, texture cache %u\\n", (unsigned)port_arena_size(), TEXTURE_BUDGET);
            if (n7_ab_preflight()) { port_log_flush(LOG_PATH); sDead = 1; return; }   """ + MARK + """
            load_manifest();
"""

_BUDGET_OLD = """            assets_set_texture_budget(TEXTURE_BUDGET);
"""
_BUDGET_NEW = """            assets_set_texture_budget(n7_ab_texture_budget(TEXTURE_BUDGET));   """ + MARK + """
"""

_INIT_OLD = """            port_crumb("init", (uint32_t)rc, hb_os_heap_free());
            port_log_flush(LOG_PATH);
"""
_INIT_NEW = """            port_crumb("init", (uint32_t)rc, hb_os_heap_free());
            n7_ab_after_init(rc);   """ + MARK + """
            port_log_flush(LOG_PATH);
"""

_RUN_OLD = """    {
        PlatTouches t;
        port_perf_flush();
"""
_RUN_NEW = """    {
        PlatTouches t;
        if (game_failed()) { n7_ab_dead_screen(w, h); return; }   """ + MARK + """
        port_perf_flush();
"""

_TICK_OLD = """        keep_awake();
        read_touches(&t);
"""
_TICK_NEW = """        keep_awake();
        n7_ab_tick();   """ + MARK + """
        read_touches(&t);
"""

# ---- gfx.c (shared with the host build, so nano only)

_GEN_OLD = """    glGenTextures(1, &name);
    if (!name)
        return 0;
"""
_GEN_NEW = """    glGenTextures(1, &name);
    if (!name)
        return 0;
#ifdef AB_NANO
    { extern void n7_ab_gl_name(uint32_t); n7_ab_gl_name(name); }   """ + MARK + """
#endif
"""

# ---- port/nano/Makefile

_MAKE_OLD = """NANO_C   := nano_platform nano_libc nano_math nano_perf nano_alloc nano_memory
"""
_MAKE_NEW = """NANO_C   := nano_platform nano_libc nano_math nano_perf nano_alloc nano_memory notice gl_left n7_ab
"""

EDITS: Dict[str, List[tuple]] = {
    "port/nano/app/ab_entry.c": [(_ENTRY_CRUMB_OLD, _ENTRY_CRUMB_NEW), (_FPU_OLD, _FPU_NEW)],
    "port/nano/nano_platform.c": [
        (_INC_OLD, _INC_NEW),
        (_DEAD_OLD, _DEAD_NEW),
        (_PREFLIGHT_OLD, _PREFLIGHT_NEW),
        (_BUDGET_OLD, _BUDGET_NEW),
        (_INIT_OLD, _INIT_NEW),
        (_RUN_OLD, _RUN_NEW),
        (_TICK_OLD, _TICK_NEW),
    ],
    "port/src/gfx.c": [(_GEN_OLD, _GEN_NEW)],
    "port/nano/Makefile": [(_MAKE_OLD, _MAKE_NEW)],
}


def apply(repo: Path, *, restore: bool = True, kit: Path = None) -> List[str]:
    """Restore the pinned files, then apply nano7kit's edits. Returns the edited paths."""
    repo = Path(repo)
    kit = Path(kit) if kit is not None else root()
    if restore and (repo / ".git").exists():
        subprocess.check_call(["git", "-C", str(repo), "checkout", "--quiet", "--", *PATCHED_FILES])
    done = []
    for rel, edits in EDITS.items():
        path = repo / rel
        if not path.is_file():
            raise Refused(f"nano-angrybirds changed: {rel} is missing. Nothing was staged.")
        text = path.read_text(encoding="utf-8")
        if MARK in text:
            raise Refused(f"{rel} is already patched and could not be restored; delete {repo} and re-run")
        for old, new in edits:
            if text.count(old) != 1:
                raise Refused(
                    f"nano-angrybirds changed: nano7kit's edit no longer matches {rel}. "
                    "Nothing was staged."
                )
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
        done.append(rel)
    for folder, name in OWN_FILES:
        shutil.copyfile(kit / folder / name, repo / "port" / "nano" / name)
    return done


def glue_numbers(kit: Path = None) -> Dict[str, int]:
    """The memory numbers in games/angrybirds/nano/n7_ab.c (one source for C and Python)."""
    kit = Path(kit) if kit is not None else root()
    text = (kit / "games" / "angrybirds" / "nano" / "n7_ab.c").read_text(encoding="utf-8")
    out = {}
    for key in ("N7_ARENA_NEED", "N7_TEX_MAX", "N7_TEX_MIN", "N7_DRIVER"):
        m = re.search(r"#define %s\s+(0x[0-9A-Fa-f]+)u" % key, text)
        if not m:
            raise Refused(f"games/angrybirds/nano/n7_ab.c lost its {key} line")
        out[key] = int(m.group(1), 16)
    return out
