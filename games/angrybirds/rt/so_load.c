/* so_load.c: mmap an Android ELF32 shared object, apply relocs, resolve imports
 * (nano7kit, MIT). Used by the qemu-arm host harness. Device builds convert the
 * .so to an ET_REL object at install time instead (tools/so_to_obj.py). */
#include "so_load.h"
#include "rt_plat.h"
#include "shims.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef FN_NANO
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#define EI_NIDENT 16
#define ET_DYN 3
#define EM_ARM 40
#define PT_LOAD 1
#define PT_DYNAMIC 2
#define PT_ARM_EXIDX 0x70000001
#define DT_NULL 0
#define DT_SYMTAB 6
#define DT_STRTAB 5
#define DT_STRSZ 10
#define DT_SYMENT 11
#define DT_REL 17
#define DT_RELSZ 18
#define DT_JMPREL 23
#define DT_PLTRELSZ 2
#define DT_INIT_ARRAY 25
#define DT_INIT_ARRAYSZ 27
#define R_ARM_ABS32 2
#define R_ARM_GLOB_DAT 21
#define R_ARM_JUMP_SLOT 22
#define R_ARM_RELATIVE 23
#define STB_LOCAL 0
#define STT_FUNC 2

typedef struct {
    unsigned char e_ident[EI_NIDENT];
    uint16_t e_type, e_machine;
    uint32_t e_version, e_entry, e_phoff, e_shoff, e_flags;
    uint16_t e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx;
} Elf32_Ehdr;
typedef struct {
    uint32_t p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align;
} Elf32_Phdr;
typedef struct {
    uint32_t r_offset, r_info;
} Elf32_Rel;
typedef struct {
    uint32_t st_name, st_value, st_size;
    unsigned char st_info, st_other;
    uint16_t st_shndx;
} Elf32_Sym;
typedef struct {
    uint32_t d_tag;
    union { uint32_t d_val; uint32_t d_ptr; } d_un;
} Elf32_Dyn;

#define ELF32_R_SYM(i) ((i) >> 8)
#define ELF32_R_TYPE(i) ((unsigned char)(i))

so_image g_so;

static uint32_t dyn_val(const Elf32_Dyn *d, int n, uint32_t tag) {
    int i;
    for (i = 0; i < n; i++) if (d[i].d_tag == tag) return d[i].d_un.d_ptr;
    return 0;
}

static void apply_rel(so_image *img, Elf32_Rel *rel, int n, Elf32_Sym *sym, const char *str) {
    int i;
    for (i = 0; i < n; i++) {
        uint32_t *loc = (uint32_t *)(img->base + rel[i].r_offset);
        unsigned type = ELF32_R_TYPE(rel[i].r_info);
        unsigned si = ELF32_R_SYM(rel[i].r_info);
        if (type == R_ARM_RELATIVE) {
            *loc += (uint32_t)(uintptr_t)img->base;
        } else if (type == R_ARM_ABS32 || type == R_ARM_GLOB_DAT || type == R_ARM_JUMP_SLOT) {
            const char *name = str + sym[si].st_name;
            if (sym[si].st_shndx == 0) {
                void *p = ab_resolve(name);
                if (!p) {
                    char b[160];
                    snprintf(b, sizeof b, "so_load: unresolved import %s", name);
                    plat_log_line(b);
                    plat_fatal(b);
                }
                /* JUMP_SLOT / GLOB_DAT replace the slot. ABS32 keeps the file
                 * addend (terminate's stderr is __sF+0xa8). Data objects that
                 * bionic exposes as pointers (_ctype_ etc.) resolve to the
                 * address of that pointer. */
                if (type == R_ARM_ABS32)
                    *loc += (uint32_t)(uintptr_t)p;
                else
                    *loc = (uint32_t)(uintptr_t)p;
            } else {
                *loc = (uint32_t)(uintptr_t)img->base + sym[si].st_value;
            }
        }
    }
}

#ifdef FN_NANO
extern uint8_t __ab_so_base[], __ab_so_end[];
extern uint32_t ab_so_exidx_off, ab_so_exidx_count, ab_so_init_off, ab_so_init_count;
int so_load(const char *path, so_image *out) {
    (void)path;
    memset(out, 0, sizeof *out);
    out->base = __ab_so_base;
    out->map_size = (size_t)(__ab_so_end - __ab_so_base);
    out->exidx_off = ab_so_exidx_off;
    out->exidx_count = ab_so_exidx_count;
    out->init_off = ab_so_init_off;
    out->init_count = ab_so_init_count;
    out->loaded = 1;
    g_so = *out;
    ab_install_cxa_hook();
    return 0;
}
#else
int so_load(const char *path, so_image *out) {
    int fd;
    struct stat st;
    uint8_t *file;
    Elf32_Ehdr *eh;
    Elf32_Phdr *ph;
    uint32_t lo = 0xffffffffu, hi = 0, dyn_off = 0, dyn_sz = 0;
    int i, n_dyn;
    Elf32_Dyn *dyn;
    memset(out, 0, sizeof *out);
    fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    if (fstat(fd, &st) || st.st_size < (off_t)sizeof(Elf32_Ehdr)) { close(fd); return -1; }
    file = mmap(0, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (file == MAP_FAILED) return -1;
    eh = (Elf32_Ehdr *)file;
    if (memcmp(eh->e_ident, "\177ELF", 4) || eh->e_type != ET_DYN || eh->e_machine != EM_ARM) {
        munmap(file, (size_t)st.st_size);
        return -1;
    }
    ph = (Elf32_Phdr *)(file + eh->e_phoff);
    for (i = 0; i < eh->e_phnum; i++) {
        if (ph[i].p_type == PT_LOAD) {
            if (ph[i].p_vaddr < lo) lo = ph[i].p_vaddr;
            if (ph[i].p_vaddr + ph[i].p_memsz > hi) hi = ph[i].p_vaddr + ph[i].p_memsz;
        } else if (ph[i].p_type == PT_DYNAMIC) {
            dyn_off = ph[i].p_offset;
            dyn_sz = ph[i].p_filesz;
        } else if (ph[i].p_type == PT_ARM_EXIDX) {
            out->exidx_off = ph[i].p_vaddr;
            out->exidx_count = ph[i].p_memsz / 8;
        }
    }
    out->map_size = (hi + 0xfffu) & ~0xfffu;
    out->base = mmap(0, out->map_size, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (out->base == MAP_FAILED) { munmap(file, (size_t)st.st_size); return -1; }
    memset(out->base, 0, out->map_size);
    for (i = 0; i < eh->e_phnum; i++) {
        if (ph[i].p_type != PT_LOAD) continue;
        memcpy(out->base + ph[i].p_vaddr, file + ph[i].p_offset, ph[i].p_filesz);
    }
    dyn = (Elf32_Dyn *)(file + dyn_off);
    n_dyn = (int)(dyn_sz / sizeof(Elf32_Dyn));
    {
        uint32_t str_off = dyn_val(dyn, n_dyn, DT_STRTAB);
        uint32_t sym_off = dyn_val(dyn, n_dyn, DT_SYMTAB);
        uint32_t rel_off = dyn_val(dyn, n_dyn, DT_REL);
        uint32_t rel_sz = dyn_val(dyn, n_dyn, DT_RELSZ);
        uint32_t jmprel = dyn_val(dyn, n_dyn, DT_JMPREL);
        uint32_t plt_sz = dyn_val(dyn, n_dyn, DT_PLTRELSZ);
        const char *str = (const char *)(out->base + str_off);
        Elf32_Sym *sym = (Elf32_Sym *)(out->base + sym_off);
        if (rel_off && rel_sz)
            apply_rel(out, (Elf32_Rel *)(out->base + rel_off), (int)(rel_sz / 8), sym, str);
        if (jmprel && plt_sz)
            apply_rel(out, (Elf32_Rel *)(out->base + jmprel), (int)(plt_sz / 8), sym, str);
        out->init_off = dyn_val(dyn, n_dyn, DT_INIT_ARRAY);
        out->init_count = dyn_val(dyn, n_dyn, DT_INIT_ARRAYSZ) / 4;
    }
    __builtin___clear_cache((char *)out->base, (char *)out->base + out->map_size);
    munmap(file, (size_t)st.st_size);
    out->loaded = 1;
    g_so = *out;
    ab_install_cxa_hook();
    return 0;
}
#endif

void *so_sym(const so_image *img, const char *name) {
    /* Walk .dynsym via the mapped image: first LOAD starts at vaddr 0. */
    Elf32_Ehdr *eh;
    Elf32_Phdr *ph;
    Elf32_Dyn *dyn = 0;
    int i, n_dyn = 0;
    if (!img || !img->base) return 0;
    eh = (Elf32_Ehdr *)img->base;
    ph = (Elf32_Phdr *)(img->base + eh->e_phoff);
    for (i = 0; i < eh->e_phnum; i++) if (ph[i].p_type == PT_DYNAMIC) {
        dyn = (Elf32_Dyn *)(img->base + ph[i].p_vaddr);
        n_dyn = (int)(ph[i].p_memsz / sizeof(Elf32_Dyn));
        break;
    }
    if (!dyn) return 0;
    {
        uint32_t str_off = dyn_val(dyn, n_dyn, DT_STRTAB);
        uint32_t sym_off = dyn_val(dyn, n_dyn, DT_SYMTAB);
        uint32_t syment = dyn_val(dyn, n_dyn, DT_SYMENT);
        const char *str = (const char *)(img->base + str_off);
        Elf32_Sym *sym = (Elf32_Sym *)(img->base + sym_off);
        /* Hash table walk would be nicer; a linear scan of dynsym is fine (4k symbols). */
        int nsym = 0;
        uint32_t hash_off = 0;
        for (i = 0; i < n_dyn; i++) if (dyn[i].d_tag == 4) { hash_off = dyn[i].d_un.d_ptr; break; }
        if (hash_off) {
            uint32_t *h = (uint32_t *)(img->base + hash_off);
            nsym = (int)h[1];
        }
        if (nsym <= 0) nsym = 5000;
        if (!syment) syment = 16;
        for (i = 0; i < nsym; i++) {
            Elf32_Sym *s = (Elf32_Sym *)((uint8_t *)sym + i * syment);
            if (s->st_name && s->st_shndx && strcmp(str + s->st_name, name) == 0)
                return img->base + s->st_value;
        }
    }
    return 0;
}

void so_run_init(const so_image *img) {
    uint32_t i;
    if (!img || !img->init_off || !img->init_count) return;
    for (i = 0; i < img->init_count; i++) {
        uint32_t fn = *(uint32_t *)(img->base + img->init_off + i * 4);
        if (fn) ((void (*)(void))(uintptr_t)fn)();
    }
}
