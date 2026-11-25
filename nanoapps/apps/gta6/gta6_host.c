/*
 * Host harness: iPod-like heaps, scripted play, PPM dumps, save tests.
 * Compile: make -C nanoapps/apps/gta6 host
 */
#include "gta6_game.h"
#include "gta6_notice.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define FB_N (G6_SCR_W * G6_SCR_H)

static void write_ppm(const char *path, const uint32_t *fb, int w, int h)
{
    FILE *f = fopen(path, "wb");
    int i;
    if (!f) {
        perror(path);
        return;
    }
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    for (i = 0; i < w * h; i++) {
        unsigned v = fb[i];
        unsigned char rgb[3] = {
            (unsigned char)((v >> 16) & 255),
            (unsigned char)((v >> 8) & 255),
            (unsigned char)(v & 255)
        };
        fwrite(rgb, 1, 3, f);
    }
    fclose(f);
}

static int load_file(const char *path, uint8_t *buf, int cap)
{
    FILE *f = fopen(path, "rb");
    size_t n;
    if (!f) return -1;
    n = fread(buf, 1, (size_t)cap, f);
    fclose(f);
    return (int)n;
}

static int save_file(const char *path, const void *buf, size_t n)
{
    FILE *f = fopen(path, "wb");
    if (!f) return 0;
    fwrite(buf, 1, n, f);
    fclose(f);
    return 1;
}

static void tap(G6Game *g, int x, int y, int frames)
{
    G6Finger f;
    int i;
    f.id = 0; f.x = x; f.y = y; f.down = 1;
    for (i = 0; i < frames; i++) {
        g6_set_fingers(g, &f, 1);
        g6_tick(g);
    }
    f.down = 0;
    g6_set_fingers(g, &f, 0);
    g6_tick(g);
}

static void two_finger(G6Game *g, int x0, int y0, int x1, int y1, int frames)
{
    G6Finger f[2];
    int i;
    f[0].id = 0; f[0].x = x0; f[0].y = y0; f[0].down = 1;
    f[1].id = 1; f[1].x = x1; f[1].y = y1; f[1].down = 1;
    for (i = 0; i < frames; i++) {
        g6_set_fingers(g, f, 2);
        g6_tick(g);
    }
}

static void idle(G6Game *g, int frames)
{
    int i;
    g6_set_fingers(g, 0, 0);
    for (i = 0; i < frames; i++) g6_tick(g);
}

static int run_demo(G6Game *g, uint32_t *fb, const char *dir)
{
    char path[512];
    idle(g, 125);
    snprintf(path, sizeof path, "%s/title.ppm", dir);
    g6_draw(g, fb, G6_SCR_W, G6_SCR_H);
    write_ppm(path, fb, G6_SCR_W, G6_SCR_H);

    tap(g, 120, 330, 2);
    idle(g, 4);
    g6_debug_drive(g, 1);
    two_finger(g, 48, 350, 198, 350, 40);
    g6_draw(g, fb, G6_SCR_W, G6_SCR_H);
    snprintf(path, sizeof path, "%s/play.ppm", dir);
    write_ppm(path, fb, G6_SCR_W, G6_SCR_H);

    g6_debug_pose(g, 32 * 16 + 8, 21 * 16 + 8, 1);
    g6_debug_wanted(g, 4);
    idle(g, 8);
    two_finger(g, 70, 360, 198, 350, 12);
    g6_draw(g, fb, G6_SCR_W, G6_SCR_H);
    snprintf(path, sizeof path, "%s/chase.ppm", dir);
    write_ppm(path, fb, G6_SCR_W, G6_SCR_H);

    g6_debug_view_map(g, 1);
    g6_draw(g, fb, G6_SCR_W, G6_SCR_H);
    snprintf(path, sizeof path, "%s/map.ppm", dir);
    write_ppm(path, fb, G6_SCR_W, G6_SCR_H);
    g6_debug_view_map(g, 0);

    g6_debug_mission(g, 1, 1);
    g6_debug_pose(g, 30 * 16 + 8, 50 * 16 + 8, 0);
    idle(g, 8);
    g6_draw(g, fb, G6_SCR_W, G6_SCR_H);
    snprintf(path, sizeof path, "%s/mission.ppm", dir);
    write_ppm(path, fb, G6_SCR_W, G6_SCR_H);
    return 0;
}

static int run_stick(G6Game *g, int ms, int fps)
{
    G6Finger f;
    int frames, i, x0, y0, x1, y1, dx, dy, dt, inb;
    if (fps < 1) fps = 1;
    if (ms < 1) ms = 1;
    g6_debug_pose(g, 32 * G6_TILE + 8, 40 * G6_TILE + 8, 0);
    x0 = g6_player_px(g);
    y0 = g6_player_py(g);
    f.id = 0;
    f.x = 48;
    f.y = 378 - 36;
    f.down = 1;
    frames = (ms * fps) / 1000;
    if (frames < 1) frames = 1;
    dt = 1000 / fps;
    if (dt < 1) dt = 1;
    for (i = 0; i < frames; i++) {
        g6_set_fingers(g, &f, 1);
        g6_advance(g, (uint32_t)dt);
    }
    x1 = g6_player_px(g);
    y1 = g6_player_py(g);
    dx = x1 - x0;
    dy = y1 - y0;
    inb = (x1 >= 8 && y1 >= 8 && x1 <= G6_WORLD_W - 8 && y1 <= G6_WORLD_H - 8);
    printf("STICK fps=%d ms=%d dx=%d dy=%d in_bounds=%d px=%d py=%d ticks=%u\n",
           fps, ms, dx, dy, inb, x1, y1, g6_ticks(g));
    return inb ? 0 : 1;
}

static int long_play(G6Game *g)
{
    uint32_t peak0, i;
    g6_debug_drive(g, 1);
    peak0 = g6_arena_peak(g);
    for (i = 0; i < 8000; i++) {
        int x = 48 + (int)((i / 17) % 40) - 20;
        int y = 360;
        G6Finger f[2];
        f[0].id = 0; f[0].x = x; f[0].y = y; f[0].down = 1;
        f[1].id = 1; f[1].x = 198; f[1].y = (i & 32) ? 350 : 402; f[1].down = 1;
        g6_set_fingers(g, f, 2);
        g6_tick(g);
        if ((i % 400) == 0 && g6_wanted(g) < 3) g6_debug_wanted(g, 3);
    }
    if (g6_arena_peak(g) != peak0) {
        fprintf(stderr, "LEAK peak %u -> %u\n", peak0, g6_arena_peak(g));
        return 1;
    }
    printf("LONG_PLAY ticks=%u peak=%u money=%d wanted=%d\n",
           g6_ticks(g), g6_arena_peak(g), g6_money(g), g6_wanted(g));
    return 0;
}

int main(int argc, char **argv)
{
    uint32_t free_b = 6u * 1024u * 1024u;
    uint32_t largest = 4u * 1024u * 1024u;
    uint32_t arena;
    G6Game *g;
    uint32_t *fb;
    const char *dump = 0;
    const char *save_path = 0;
    const char *load_path = 0;
    int corrupt = 0;
    int nosave = 0;
    int do_long = 0;
    int do_demo = 0;
    int do_home = 0;
    int stick_ms = 0;
    int stick_fps = 30;
    int fail = 0;
    int i;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--free") && i + 1 < argc) free_b = (uint32_t)strtoul(argv[++i], 0, 10);
        else if (!strcmp(argv[i], "--largest") && i + 1 < argc) largest = (uint32_t)strtoul(argv[++i], 0, 10);
        else if (!strcmp(argv[i], "--dump") && i + 1 < argc) dump = argv[++i];
        else if (!strcmp(argv[i], "--save") && i + 1 < argc) save_path = argv[++i];
        else if (!strcmp(argv[i], "--load") && i + 1 < argc) load_path = argv[++i];
        else if (!strcmp(argv[i], "--corrupt")) corrupt = 1;
        else if (!strcmp(argv[i], "--nosave")) nosave = 1;
        else if (!strcmp(argv[i], "--long")) do_long = 1;
        else if (!strcmp(argv[i], "--demo")) do_demo = 1;
        else if (!strcmp(argv[i], "--home")) do_home = 1;
        else if (!strcmp(argv[i], "--stick-ms") && i + 1 < argc) stick_ms = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--fps") && i + 1 < argc) stick_fps = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--tight")) {
            free_b = (uint32_t)(3.9 * 1024 * 1024);
            largest = 2u * 1024u * 1024u;
        }
        else if (!strcmp(argv[i], "--tiny")) {
            free_b = 400000;
            largest = 80000;
        }
    }

    if (dump)
        mkdir(dump, 0755);

    arena = g6_pick_block(free_b, largest);
    printf("HEAP free=%u largest=%u arena=%u sizeof=%u\n",
           (unsigned)free_b, (unsigned)largest, (unsigned)arena, (unsigned)g6_size());
    if (!arena) {
        G6Game dummy;
        memset(&dummy, 0, sizeof dummy);
        dummy.low_mem = 1;
        dummy.mode = G6_SHORT;
        if (dump) {
            fb = (uint32_t *)malloc(FB_N * 4);
            if (!fb) return 2;
            g6_draw(&dummy, fb, G6_SCR_W, G6_SCR_H);
            {
                char path[512];
                snprintf(path, sizeof path, "%s/lowmem.ppm", dump);
                write_ppm(path, fb, G6_SCR_W, G6_SCR_H);
            }
            free(fb);
        }
        printf("VERDICT=NO_FIT\n");
        return 0;
    }
    printf("VERDICT=FITS\n");
    g = (G6Game *)malloc(arena);
    fb = (uint32_t *)malloc(FB_N * 4);
    if (!g || !fb) return 2;
    memset(g, 0, arena);
    if (!g6_init(g, free_b, largest, arena)) {
        printf("INIT_FAIL\n");
        fail = 1;
        goto done;
    }
    if (load_path && !nosave) {
        uint8_t buf[128];
        int n = load_file(load_path, buf, (int)sizeof buf);
        int rc;
        if (corrupt && n > 8) buf[8] ^= 0xff;
        rc = g6_load(g, n > 0 ? buf : 0, n > 0 ? (size_t)n : 0);
        printf("LOAD rc=%d bad=%d ticker=%s\n", rc, g->save_bad, g6_ticker(g));
    } else if (nosave) {
        printf("LOAD rc=-1 nosave ticker=%s\n", g6_ticker(g));
    }

    if (do_demo && dump) run_demo(g, fb, dump);
    if (do_long) fail |= long_play(g);
    if (do_home) {
        tap(g, 120, 330, 1);
        tap(g, 120, 330, 1);
        tap(g, 210, 10, 1);
        tap(g, 120, 280, 1);
        printf("HOME want=%d mode=%d\n", g6_want_home(g), g6_mode(g));
        if (!g6_want_home(g)) fail = 1;
    }
    if (stick_ms) fail |= run_stick(g, stick_ms, stick_fps);
    if (save_path) {
        uint8_t buf[128];
        size_t n = 0;
        if (g6_mode(g) == G6_TITLE_MODE) tap(g, 120, 330, 2);
        g6_save(g, buf, sizeof buf, &n);
        save_file(save_path, buf, n);
        printf("SAVE bytes=%u\n", (unsigned)n);
    }
    printf("PEAK %u ticks=%u mode=%d money=%d who=%s\n",
           g6_arena_peak(g), g6_ticks(g), g6_mode(g), g6_money(g), g6_who_name(g));

done:
    free(g);
    free(fb);
    return fail;
}
