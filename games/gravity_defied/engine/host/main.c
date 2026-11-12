/* Host harness for Gravity Defied. Mimics the iPod: one arena, no
 * exception recovery, the same notice arithmetic as gd_nano.c. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <errno.h>

#include "gd_config.h"
#include "gdalloc.h"
#include "gd_engine.h"
#include "notice.h"

#define GD_SCREEN_W 240
#define GD_SCREEN_H 432
#define BLOCK_MAX 0x280000u
#define BLOCK_MIN 0x200000u
#define AFTER_MIN 0x20000u
#define SLOP      0x10000u

static uint32_t fb[GD_SCREEN_W * GD_SCREEN_H];
static int want_notice;
static uint32_t sim_free = 6u * 1024u * 1024u;
static uint32_t sim_largest = 4u * 1024u * 1024u;
static uint32_t arena_size = BLOCK_MAX;
static char data_dir[512];
static char ppm_dir[512];
static unsigned max_frames = 4000;
static int dump_every;
static FILE *script;

static uint32_t pick_block(uint32_t free_b, uint32_t largest)
{
    uint32_t size;
    if (largest > BLOCK_MAX + SLOP) size = BLOCK_MAX;
    else size = (largest > SLOP) ? ((largest - SLOP) & ~0xFFFFu) : 0;
    if (free_b > AFTER_MIN) {
        uint32_t cap = (free_b - AFTER_MIN) & ~0xFFFFu;
        if (size > cap) size = cap;
    } else {
        size = 0;
    }
    if (largest < BLOCK_MIN + SLOP || size < BLOCK_MIN) return 0;
    return size;
}

static void write_ppm(const char *path)
{
    FILE *f = fopen(path, "wb");
    int i, n;
    if (!f) return;
    fprintf(f, "P6\n%d %d\n255\n", GD_SCREEN_W, GD_SCREEN_H);
    n = GD_SCREEN_W * GD_SCREEN_H;
    for (i = 0; i < n; i++) {
        unsigned c = fb[i];
        unsigned char rgb[3] = {
            (unsigned char)((c >> 16) & 255),
            (unsigned char)((c >> 8) & 255),
            (unsigned char)(c & 255),
        };
        fwrite(rgb, 1, 3, f);
    }
    fclose(f);
}

static void draw_bar(void)
{
    int y, x, i;
    static const char *lab[5] = { "BRK", "<", "MENU", ">", "GAS" };
    for (y = GD_GAME_H; y < GD_SCREEN_H; y++) {
        uint32_t *row = fb + (long)y * GD_SCREEN_W;
        for (x = 0; x < GD_SCREEN_W; x++) {
            int zone = x / 48;
            uint32_t c = 0xff202028u;
            if (zone <= 0) c = gd_held_brake() ? 0xff803010u : 0xff402018u;
            else if (zone == 1) c = gd_held_lean_l() ? 0xff306080u : 0xff203040u;
            else if (zone == 2) c = 0xff303038u;
            else if (zone == 3) c = gd_held_lean_r() ? 0xff306080u : 0xff203040u;
            else c = gd_held_gas() ? 0xff108030u : 0xff184020u;
            row[x] = c | 0xff000000u;
        }
    }
    (void)lab;
    (void)i;
}

static int run_line(char *line, unsigned frame)
{
    char cmd[64];
    int a = 0, b = 0, c = 0, d = 0;
    if (line[0] == '#' || line[0] == 0) return 0;
    if (sscanf(line, "%63s %d %d %d %d", cmd, &a, &b, &c, &d) < 1) return 0;
    if (strcmp(cmd, "hold") == 0) {
        gd_host_set_buttons(a, b, c, d);
        return 0;
    }
    if (strcmp(cmd, "tap") == 0) {
        gd_set_edges(1, 0, 0, a, b);
        gd_log("input: tap %d,%d frame=%u", a, b, frame);
        return 0;
    }
    if (strcmp(cmd, "pause") == 0) {
        gd_set_edges(0, 0, 1, -1, -1);
        gd_log("input: pause frame=%u", frame);
        return 0;
    }
    if (strcmp(cmd, "fire") == 0) {
        gd_host_key(8);
        return 0;
    }
    if (strcmp(cmd, "key") == 0) {
        gd_host_key(a);
        return 0;
    }
    if (strcmp(cmd, "keyup") == 0) {
        gd_host_key_up(a);
        return 0;
    }
    if (strcmp(cmd, "wait") == 0) return a > 0 ? a : 1;
    if (strcmp(cmd, "dump") == 0) {
        char name[64];
        char path[640];
        if (sscanf(line, "dump %63s", name) != 1)
            snprintf(name, sizeof name, "frame");
        snprintf(path, sizeof path, "%s/%s.ppm", ppm_dir[0] ? ppm_dir : ".", name);
        write_ppm(path);
        gd_log("dump: %s", path);
        return 0;
    }
    if (strcmp(cmd, "quit") == 0) {
        gd_engine_quit();
        return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    int i;
    void *arena;
    unsigned frame = 0;
    int wait_left = 0;
    char line[256];
    const char *mrg = "levels.mrg";

    snprintf(data_dir, sizeof data_dir, ".");
    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--data") == 0 && i + 1 < argc) snprintf(data_dir, sizeof data_dir, "%s", argv[++i]);
        else if (strcmp(argv[i], "--mrg") == 0 && i + 1 < argc) mrg = argv[++i];
        else if (strcmp(argv[i], "--arena") == 0 && i + 1 < argc) arena_size = (uint32_t)strtoul(argv[++i], 0, 0);
        else if (strcmp(argv[i], "--free") == 0 && i + 1 < argc) sim_free = (uint32_t)strtoul(argv[++i], 0, 0);
        else if (strcmp(argv[i], "--largest") == 0 && i + 1 < argc) sim_largest = (uint32_t)strtoul(argv[++i], 0, 0);
        else if (strcmp(argv[i], "--frames") == 0 && i + 1 < argc) max_frames = (unsigned)strtoul(argv[++i], 0, 0);
        else if (strcmp(argv[i], "--ppm") == 0 && i + 1 < argc) snprintf(ppm_dir, sizeof ppm_dir, "%s", argv[++i]);
        else if (strcmp(argv[i], "--script") == 0 && i + 1 < argc) {
            script = fopen(argv[++i], "r");
            if (!script) {
                fprintf(stderr, "cannot open script\n");
                return 2;
            }
        } else if (strcmp(argv[i], "--notice") == 0) want_notice = 1;
        else {
            fprintf(stderr, "unknown arg %s\n", argv[i]);
            return 2;
        }
    }

    mkdir(data_dir, 0777);
    if (ppm_dir[0]) mkdir(ppm_dir, 0777);
    gd_set_data_dir(data_dir);
    gd_set_fb(fb, GD_SCREEN_W, GD_SCREEN_H);
    gd_log("heap: free=%u largest=%u (simulated iPod Probe numbers after image)",
           (unsigned)sim_free, (unsigned)sim_largest);

    {
        uint32_t size = pick_block(sim_free, sim_largest);
        if (want_notice) size = 0;
        if (!size) {
            const char *lines[4] = {
                "NOT ENOUGH MEMORY", "PAUSE MUSIC", "TURN BLUETOOTH OFF", "THEN REOPEN"
            };
            gd_log("heap: NOT ENOUGH MEMORY free=%u largest=%u", (unsigned)sim_free, (unsigned)sim_largest);
            notice_draw_fb(fb, GD_SCREEN_W, GD_SCREEN_H, lines, 4);
            if (ppm_dir[0]) {
                char path[640];
                snprintf(path, sizeof path, "%s/notice.ppm", ppm_dir);
                write_ppm(path);
            }
            gd_log_flush();
            return 3;
        }
        arena_size = size;
    }

    arena = malloc(arena_size);
    if (!arena) {
        gd_log("fatal: host arena malloc failed");
        gd_log_flush();
        return 2;
    }
    if (gd_arena_init(arena, arena_size) != 0) gd_fatal("arena");
    gd_log("heap: arena %u", (unsigned)arena_size);
    gd_engine_start(mrg, data_dir);

    for (frame = 0; frame < max_frames; frame++) {
        gd_host_advance_ms(16);
        if (script && wait_left <= 0) {
            if (fgets(line, sizeof line, script)) {
                int w = run_line(line, frame);
                if (w < 0) break;
                wait_left = w;
            }
        }
        if (wait_left > 0) wait_left--;
        gd_engine_tick();
        gd_input_reset_edges();
        draw_bar();
        if (gd_want_home()) {
            gd_log("quit: path Exit Game / destroyApp (host process exit)");
            if (ppm_dir[0]) {
                char path[640];
                snprintf(path, sizeof path, "%s/menu_exit.ppm", ppm_dir);
                write_ppm(path);
                gd_log("dump: %s", path);
            }
            break;
        }
        if (dump_every && (frame % (unsigned)dump_every) == 0 && ppm_dir[0]) {
            char path[640];
            snprintf(path, sizeof path, "%s/f%05u.ppm", ppm_dir, frame);
            write_ppm(path);
        }
        if ((frame % 60u) == 59u) {
            gd_log("fps: 62 arena_used=%u peak=%u fails=%u frame=%u menu=%d loading=%d",
                   gd_arena_used(), gd_arena_peak(), gd_arena_fails(), frame,
                   gd_engine_in_menu(), gd_engine_loading());
        }
    }
    gd_log("done: frames=%u used=%u peak=%u fails=%u want_home=%d",
           frame, gd_arena_used(), gd_arena_peak(), gd_arena_fails(), gd_want_home());
    gd_log_flush();
    if (script) fclose(script);
    /* Keep the arena until process exit, same as HB_APP_AUX_BLOCK on the iPod. */
    (void)arena;
    return gd_want_home() ? 0 : 1;
}
