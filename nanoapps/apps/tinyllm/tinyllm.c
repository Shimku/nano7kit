/* SPDX-License-Identifier: MIT */
/*
 * Tiny LLM: a 260K-parameter story model running on the iPod nano 7G.
 *
 * RAW_SURFACE app. The model file (/Apps/Data/TinyLLM/model.bin, made by
 * mkmodel.py from karpathy/tinyllamas stories260K) is read into one OS heap
 * block together with the engine's work area; nothing is written back.
 *
 * Screens: prompt menu, on-screen keyboard, story. Vol +/- move, Play/Pause
 * picks; every screen also works by touch. Home is left to the iPod.
 */
#include "hb_raw_surface.h"
#include "hb_sdk.h"
#include "hb_heap.h"
#include "tllm.h"

#if defined(__arm__) && !defined(__clang__)
/* Full-screen redraws are the other hot loop; -O2 like tllm.c. */
#pragma GCC optimize("O2", "no-tree-loop-distribute-patterns")
#endif

#define SCREEN_W 240
#define SCREEN_H 432

#define DATA_DIR   "/Apps/Data/TinyLLM"
#define MODEL_PATH DATA_DIR "/model.bin"

/* Memory policy: the whole arena is one block. Keep some slack in the
 * largest block and leave the OS at least RESERVE free afterwards. */
#define SLOP    (64u * 1024u)
#define RESERVE (512u * 1024u)

#define TEMP 0.9f
#define TOPP 0.9f
#define STORY_TOKENS 256

#define REVEAL_CPS  60      /* typing speed of the text on screen */
#define BACKLOG     48      /* engine waits while this many chars are unshown */
#define BUDGET_US   20000u  /* engine time per frame */

/* colours */
#define C_BG      0x0b0f14u
#define C_BG2     0x0e131au   /* every other row: a faint scanline */
#define C_BAR     0x1a2230u
#define C_TITLE   0xffb000u   /* amber */
#define C_PROMPT  0xffb000u
#define C_STORY   0x7cfc9au   /* terminal green */
#define C_DIM     0x8a93a8u
#define C_TEXT    0xe8e6d9u
#define C_SEL     0x2b5d3au   /* selected menu row */
#define C_KEY     0x232c3bu
#define C_KEYDN   0x3f6f4fu
#define C_RED     0xff5555u
#define C_AMBERW  0xffaa00u
#define C_BLUE    0x55aaffu

enum { ST_LOWMEM, ST_MISSING, ST_DAMAGED, ST_MENU, ST_KEYS, ST_STORY };

static const char *const k_prompts[] = {
    "Once upon a time",
    "Lily and Ben went to",
    "The little dog was",
    "Tim found a box",
    "One day, a cat",
};
#define N_PROMPTS 5
#define ITEM_SURPRISE N_PROMPTS
#define ITEM_TYPE     (N_PROMPTS + 1)
#define N_ITEMS       (N_PROMPTS + 2)

static uint32_t *s_fb;
static int s_w, s_h;
static int s_state;
static int s_dirty;
static uint32_t s_free, s_largest, s_need;
static int s_err;

static Tllm s_t;
static uint8_t *s_arena;

/* menu */
static int s_sel;
/* keyboard */
#define TYPE_MAX 48
static char s_typed[TYPE_MAX + 1];
static int s_ntyped;
static int s_caps;
static int s_key_down = -1;
/* story */
#define TEXT_MAX 2400
static char s_text[TEXT_MAX + 1];
static int s_len, s_prompt_len, s_shown;
static char s_cur_prompt[TYPE_MAX + 1];
static int s_running, s_paused, s_finished;
static uint32_t s_reveal_ms, s_reveal_acc;
static uint32_t s_compute_us, s_tokens;
static int s_scroll;          /* lines scrolled back from the end */
static uint32_t s_seed_ctr;
static uint32_t s_blink_ms;
static int s_blink;
/* input */
static int s_prev_btn[3];
static int s_prev_down;
static int s_tap_x, s_tap_y, s_tapped;
static int s_touch_x, s_touch_y, s_touch_down;

/* ---------------- drawing ---------------- */

static void clear_bg(void)
{
    int x, y;
    for (y = 0; y < s_h; y++) {
        uint32_t c = 0xff000000u | ((y & 1) ? C_BG2 : C_BG);
        uint32_t *row = s_fb + y * s_w;
        for (x = 0; x < s_w; x++) row[x] = c;
    }
}

static void rect(int x, int y, int w, int h, uint32_t c)
{
    int ix, iy;
    c |= 0xff000000u;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > s_w) w = s_w - x;
    if (y + h > s_h) h = s_h - y;
    for (iy = 0; iy < h; iy++) {
        uint32_t *row = s_fb + (y + iy) * s_w + x;
        for (ix = 0; ix < w; ix++) row[ix] = c;
    }
}

static void glyph(int x, int y, char ch, int scale, uint32_t fg)
{
    const uint8_t *g = hb_font8x8_glyph((unsigned char)ch);
    int r, c, sx, sy;
    fg |= 0xff000000u;
    for (r = 0; r < 8; r++) {
        uint8_t bits = g[r];
        if (!bits) continue;
        for (c = 0; c < 8; c++) {
            if (!(bits & (0x80 >> c))) continue;
            for (sy = 0; sy < scale; sy++) {
                int py = y + r * scale + sy;
                if (py < 0 || py >= s_h) continue;
                for (sx = 0; sx < scale; sx++) {
                    int px = x + c * scale + sx;
                    if (px >= 0 && px < s_w) s_fb[py * s_w + px] = fg;
                }
            }
        }
    }
}

static int text(int x, int y, const char *s, int scale, uint32_t fg)
{
    for (; *s; s++, x += 8 * scale) glyph(x, y, *s, scale, fg);
    return x;
}

static int text_w(const char *s, int scale)
{
    int n = 0;
    while (s[n]) n++;
    return n * 8 * scale;
}

static char *put_s(char *o, const char *s)
{
    while (*s) *o++ = *s++;
    *o = 0;
    return o;
}

static char *put_u(char *o, uint32_t u)
{
    char t[12];
    int n = 0;
    do { t[n++] = (char)('0' + (u % 10u)); u /= 10u; } while (u);
    while (n) *o++ = t[--n];
    *o = 0;
    return o;
}

static void header(const char *right)
{
    rect(0, 0, s_w, 28, C_BAR);
    text(8, 6, "TINY LLM", 2, C_TITLE);
    if (right) text(s_w - 8 - text_w(right, 1), 10, right, 1, C_DIM);
}

static void button(int x, int y, int w, int h, const char *label, int lit)
{
    rect(x, y, w, h, lit ? C_KEYDN : C_KEY);
    rect(x, y + h - 2, w, 2, 0x10151d);
    text(x + (w - text_w(label, 1)) / 2, y + (h - 8) / 2, label, 1, C_TEXT);
}

/* ---------------- message screens ---------------- */

static void draw_message(const char *t1, const char *t2, uint32_t ink)
{
    clear_bg();
    header(0);
    text(16, 90, t1, 2, ink);
    text(16, 114, t2, 2, ink);
}

static void draw_low_memory(void)
{
    char line[48], *o;
    draw_message("NOT ENOUGH", "MEMORY", C_RED);
    o = put_s(line, "FREE  ");
    o = put_u(o, s_free / 1024u);
    put_s(o, " KB");
    text(16, 160, line, 2, C_TEXT);
    o = put_s(line, "BLOCK ");
    o = put_u(o, s_largest / 1024u);
    put_s(o, " KB");
    text(16, 184, line, 2, C_TEXT);
    o = put_s(line, "NEEDS ");
    o = put_u(o, (s_need + SLOP) / 1024u);
    put_s(o, " KB");
    text(16, 208, line, 2, C_TEXT);
    text(16, 260, "PAUSE MUSIC OR", 2, C_TITLE);
    text(16, 284, "BLUETOOTH", 2, C_TITLE);
    text(16, 330, "THEN OPEN TINY LLM AGAIN", 1, C_DIM);
}

static void draw_missing(void)
{
    draw_message("MODEL FILE", "MISSING", C_AMBERW);
    text(16, 160, "Looked for:", 1, C_DIM);
    text(8, 176, MODEL_PATH, 1, C_TEXT);
    text(16, 210, "Copy it from the computer", 1, C_DIM);
    text(16, 222, "in Disk Mode:", 1, C_DIM);
    text(4, 244, "./start nabase add 'Tiny LLM'", 1, C_TEXT);
}

static void draw_damaged(void)
{
    char line[40], *o;
    draw_message("MODEL FILE", "DAMAGED", C_BLUE);
    o = put_s(line, "Check failed (code ");
    o = put_u(o, (uint32_t)s_err);
    put_s(o, ")");
    text(16, 160, line, 1, C_TEXT);
    text(16, 184, "Copy it again in Disk Mode:", 1, C_DIM);
    text(4, 206, "./start nabase add 'Tiny LLM'", 1, C_TEXT);
}

/* ---------------- menu ---------------- */

#define MENU_Y0 112
#define MENU_ROW 34

static const char *item_label(int i)
{
    if (i < N_PROMPTS) return k_prompts[i];
    if (i == ITEM_SURPRISE) return "Surprise me";
    return "Type your own...";
}

static void draw_menu(void)
{
    int i;
    clear_bg();
    header("260K");
    text(12, 40, "A 260K-parameter model that", 1, C_DIM);
    text(12, 52, "writes tiny stories, one", 1, C_DIM);
    text(12, 64, "token at a time.", 1, C_DIM);
    text(12, 90, "START A STORY", 1, C_TITLE);
    for (i = 0; i < N_ITEMS; i++) {
        int y = MENU_Y0 + i * MENU_ROW;
        if (i == s_sel) {
            rect(6, y, s_w - 12, MENU_ROW - 4, C_SEL);
            rect(6, y, 4, MENU_ROW - 4, C_STORY);
        } else {
            rect(6, y, s_w - 12, MENU_ROW - 4, 0x141b25);
        }
        text(18, y + 11, item_label(i), 1, i == s_sel ? C_TEXT : (i < N_PROMPTS ? C_PROMPT : C_STORY));
    }
    text(12, 370, "VOL +/-  CHOOSE", 1, C_DIM);
    text(12, 384, "PLAY     START", 1, C_DIM);
    text(12, 398, "OR TAP A LINE", 1, C_DIM);
    text(12, 418, "stories260K, llama2.c", 1, 0x4a5366);
}

/* ---------------- keyboard ---------------- */

static const char *const k_rows[3] = { "qwertyuiop", "asdfghjkl'", "zxcvbnm,.!" };
#define KEY_Y0 200
#define KEY_H  38
#define KEY_W  24
/* key codes: 0..29 letters by row, then specials */
#define K_CAPS  30
#define K_SPACE 31
#define K_DEL   32
#define K_GO    33
#define K_BACK  34

static void key_box(int k, int *x, int *y, int *w, int *h)
{
    if (k < 30) {
        *x = (k % 10) * KEY_W;
        *y = KEY_Y0 + (k / 10) * KEY_H;
        *w = KEY_W;
        *h = KEY_H;
        return;
    }
    *y = KEY_Y0 + 3 * KEY_H;
    *h = KEY_H;
    switch (k) {
    case K_CAPS:  *x = 0;   *w = 48; break;
    case K_SPACE: *x = 48;  *w = 96; break;
    case K_DEL:   *x = 144; *w = 48; break;
    default:      *x = 192; *w = 48; break; /* GO */
    }
    if (k == K_BACK) { *x = 0; *y = KEY_Y0 + 4 * KEY_H + 12; *w = 240; *h = 34; }
}

static int key_at(int px, int py)
{
    int k;
    for (k = 0; k <= K_BACK; k++) {
        int x, y, w, h;
        key_box(k, &x, &y, &w, &h);
        if (px >= x && px < x + w && py >= y && py < y + h) return k;
    }
    return -1;
}

static char key_char(int k)
{
    char c = k_rows[k / 10][k % 10];
    if ((s_caps || s_ntyped == 0) && c >= 'a' && c <= 'z') c = (char)(c - 32);
    return c;
}

static void draw_keys(void)
{
    int k, i, col, line;
    char one[2] = { 0, 0 };
    clear_bg();
    header("YOUR PROMPT");
    rect(6, 40, s_w - 12, 120, 0x141b25);
    text(12, 48, "Type the start of a story:", 1, C_DIM);
    /* typed text, wrapped at 26 columns, with a block cursor */
    col = 0;
    line = 0;
    for (i = 0; i <= s_ntyped; i++) {
        int x = 14 + col * 8, y = 70 + line * 12;
        if (i == s_ntyped) {
            rect(x, y, 7, 9, C_STORY);
            break;
        }
        one[0] = s_typed[i];
        glyph(x, y, one[0], 1, C_PROMPT);
        if (++col == 26) { col = 0; line++; }
    }
    {
        char n[16], *o = put_u(n, (uint32_t)s_ntyped);
        o = put_s(o, "/");
        put_u(o, TYPE_MAX);
        text(s_w - 12 - text_w(n, 1), 146, n, 1, 0x4a5366);
    }
    for (k = 0; k < 30; k++) {
        int x, y, w, h;
        key_box(k, &x, &y, &w, &h);
        rect(x + 1, y + 1, w - 2, h - 2, k == s_key_down ? C_KEYDN : C_KEY);
        one[0] = key_char(k);
        glyph(x + (w - 8) / 2, y + (h - 8) / 2, one[0], 1, C_TEXT);
    }
    for (k = K_CAPS; k <= K_BACK; k++) {
        int x, y, w, h;
        const char *lab = k == K_CAPS ? (s_caps ? "ABC" : "abc") : k == K_SPACE ? "space" :
                          k == K_DEL ? "DEL" : k == K_GO ? "GO" : "BACK TO MENU";
        key_box(k, &x, &y, &w, &h);
        button(x + 1, y + 1, w - 2, h - 2, lab, k == s_key_down || (k == K_CAPS && s_caps));
    }
    text(12, 418, "PLAY  GO", 1, C_DIM);
}

/* ---------------- story ---------------- */

#define COLS 29
#define LINE_H 10
#define TEXT_Y 34
#define TEXT_LINES 35
#define MAX_LINES 160

static int s_line_start[MAX_LINES];
static int s_nlines;

/* Word-wrap s_text[0..n) into lines of COLS. */
static void wrap(int n)
{
    int i = 0, start = 0, col = 0, last_space = -1;
    s_nlines = 0;
    s_line_start[s_nlines++] = 0;
    for (i = 0; i < n && s_nlines < MAX_LINES; i++) {
        char c = s_text[i];
        if (c == '\n') {
            start = i + 1;
            col = 0;
            last_space = -1;
            s_line_start[s_nlines++] = start;
            continue;
        }
        if (c == ' ') last_space = i;
        if (col == COLS) {
            if (last_space >= start && c != ' ') {
                start = last_space + 1;
            } else {
                start = i;
            }
            col = i - start;
            last_space = -1;
            s_line_start[s_nlines++] = start;
        }
        col++;
    }
}

static void draw_story(void)
{
    char line[48], *o;
    int first, ln, n = s_shown;
    uint32_t tps10 = 0;
    clear_bg();
    header(s_finished ? "THE END" : (s_paused ? "PAUSED" : "WRITING"));
    wrap(n);
    first = s_nlines - TEXT_LINES - s_scroll;
    if (first < 0) first = 0;
    for (ln = first; ln < s_nlines && ln < first + TEXT_LINES; ln++) {
        int i = s_line_start[ln];
        int end = (ln + 1 < s_nlines) ? s_line_start[ln + 1] : n;
        int col = 0;
        int y = TEXT_Y + (ln - first) * LINE_H;
        for (; i < end; i++) {
            char c = s_text[i];
            if (c == '\n') break;
            if (col == 0 && c == ' ' && ln > 0 && s_text[i - 1] != '\n') continue;
            if (c >= 32 && c < 127)
                glyph(4 + col * 8, y, c, 1, i < s_prompt_len ? C_PROMPT : C_STORY);
            col++;
        }
        if (ln == s_nlines - 1 && !s_finished && s_blink)
            rect(4 + col * 8, y, 7, 9, C_STORY);
    }
    if (s_scroll) text(s_w - 60, TEXT_Y + TEXT_LINES * LINE_H - 2, "MORE v", 1, C_DIM);

    rect(0, 388, s_w, 44, C_BAR);
    if (s_compute_us) tps10 = (uint32_t)((uint64_t)s_tokens * 10000000ull / s_compute_us);
    o = put_u(line, tps10 / 10u);
    o = put_s(o, ".");
    o = put_u(o, tps10 % 10u);
    o = put_s(o, " tok/s  ");
    o = put_u(o, (uint32_t)s_t.pos);
    o = put_s(o, "/");
    put_u(o, STORY_TOKENS);
    text(8, 392, line, 1, C_TEXT);
    button(4, 404, 74, 24, "MENU", 0);
    button(82, 404, 74, 24, s_finished ? "AGAIN" : (s_paused ? "GO ON" : "PAUSE"), 0);
    button(160, 404, 76, 24, s_finished ? "NEW" : "STOP", 0);
}

static void append(const char *s)
{
    while (*s && s_len < TEXT_MAX) {
        char c = *s++;
        if (c == '\r') continue;
        if (c != '\n' && (c < 32 || c >= 127)) continue;
        s_text[s_len++] = c;
    }
    s_text[s_len] = 0;
}

static void start_story(const char *prompt)
{
    int i;
    for (i = 0; prompt[i] && i < TYPE_MAX; i++) s_cur_prompt[i] = prompt[i];
    s_cur_prompt[i] = 0;
    s_len = s_shown = s_prompt_len = 0;
    s_text[0] = 0;
    s_seed_ctr++;
    tllm_begin(&s_t, s_cur_prompt, ((uint64_t)hb_time_uptime_us() << 16) ^ (s_seed_ctr * 0x9E3779B9u) ^ 1u,
               TEMP, TOPP, STORY_TOKENS);
    s_running = 1;
    s_paused = 0;
    s_finished = 0;
    s_compute_us = 0;
    s_tokens = 0;
    s_scroll = 0;
    s_reveal_ms = hb_time_uptime_ms();
    s_reveal_acc = 0;
    s_state = ST_STORY;
    s_dirty = 1;
}

static void run_engine(void)
{
    uint32_t t0 = hb_time_uptime_us(), now;
    if (!s_running || s_paused) return;
    for (;;) {
        int r;
        if (s_len - s_shown >= BACKLOG) break;
        r = tllm_step(&s_t);
        if (r == TLLM_TOKEN) {
            s_tokens++;
            append(s_t.piece);
            if (s_t.is_prompt) s_prompt_len = s_len;
        } else if (r == TLLM_DONE) {
            if (s_t.stage < 0) s_tokens++;
            s_running = 0;
            break;
        }
        now = hb_time_uptime_us();
        if (now - t0 >= BUDGET_US) break;
    }
    s_compute_us += hb_time_uptime_us() - t0;
}

static void reveal(void)
{
    uint32_t now = hb_time_uptime_ms(), dt = now - s_reveal_ms;
    s_reveal_ms = now;
    if (s_paused) return;
    s_reveal_acc += dt * REVEAL_CPS;
    while (s_reveal_acc >= 1000u && s_shown < s_len) {
        s_shown++;
        s_reveal_acc -= 1000u;
        s_dirty = 1;
    }
    /* the prompt shows at once */
    if (s_shown < s_prompt_len) { s_shown = s_prompt_len; s_dirty = 1; }
    if (s_shown >= s_len) s_reveal_acc = 0;
    if (!s_running && s_shown >= s_len && !s_finished) {
        s_finished = 1;
        s_dirty = 1;
    }
}

/* ---------------- input ---------------- */

static int btn_edge(int i, hb_button_t b)
{
    int down = hb_button_pressed(b) ? 1 : 0;
    int edge = down && !s_prev_btn[i];
    s_prev_btn[i] = down;
    return edge;
}

static void read_touch(const hb_spoint_t *t)
{
    int down = t && t->down;
    s_tapped = 0;
    if (t) { s_touch_x = t->x; s_touch_y = t->y; }
    s_touch_down = down;
    if (s_prev_down && !down) {
        s_tapped = 1;
        s_tap_x = s_touch_x;
        s_tap_y = s_touch_y;
    }
    s_prev_down = down;
}

static void pick_menu(int i)
{
    if (i < N_PROMPTS) {
        start_story(k_prompts[i]);
    } else if (i == ITEM_SURPRISE) {
        start_story("");
    } else {
        s_state = ST_KEYS;
        s_dirty = 1;
    }
}

static void menu_input(int up, int dn, int play)
{
    if (up) { s_sel = (s_sel + N_ITEMS - 1) % N_ITEMS; s_dirty = 1; }
    if (dn) { s_sel = (s_sel + 1) % N_ITEMS; s_dirty = 1; }
    if (s_touch_down && s_touch_y >= MENU_Y0 && s_touch_y < MENU_Y0 + N_ITEMS * MENU_ROW) {
        int i = (s_touch_y - MENU_Y0) / MENU_ROW;
        if (i != s_sel) { s_sel = i; s_dirty = 1; }
    }
    if (s_tapped && s_tap_y >= MENU_Y0 && s_tap_y < MENU_Y0 + N_ITEMS * MENU_ROW) {
        s_sel = (s_tap_y - MENU_Y0) / MENU_ROW;
        pick_menu(s_sel);
        return;
    }
    if (play) pick_menu(s_sel);
}

static void keys_input(int play)
{
    int k = s_touch_down ? key_at(s_touch_x, s_touch_y) : -1;
    if (k != s_key_down) { s_key_down = k; s_dirty = 1; }
    if (s_tapped) {
        k = key_at(s_tap_x, s_tap_y);
        s_dirty = 1;
        if (k >= 0 && k < 30) {
            if (s_ntyped < TYPE_MAX) {
                s_typed[s_ntyped] = key_char(k);
                s_typed[++s_ntyped] = 0;
            }
            s_caps = 0;
        } else if (k == K_SPACE) {
            if (s_ntyped > 0 && s_ntyped < TYPE_MAX && s_typed[s_ntyped - 1] != ' ') {
                s_typed[s_ntyped++] = ' ';
                s_typed[s_ntyped] = 0;
            }
        } else if (k == K_DEL) {
            if (s_ntyped > 0) s_typed[--s_ntyped] = 0;
        } else if (k == K_CAPS) {
            s_caps = !s_caps;
        } else if (k == K_GO) {
            play = 1;
        } else if (k == K_BACK) {
            s_state = ST_MENU;
            return;
        }
    }
    if (play) {
        int n = s_ntyped;
        while (n > 0 && s_typed[n - 1] == ' ') s_typed[--n] = 0;
        s_ntyped = n;
        start_story(s_typed);
    }
}

static void story_input(int up, int dn, int play)
{
    if (up && s_scroll + TEXT_LINES < s_nlines) { s_scroll += 4; s_dirty = 1; }
    if (dn && s_scroll > 0) { s_scroll -= 4; if (s_scroll < 0) s_scroll = 0; s_dirty = 1; }
    if (s_tapped && s_tap_y >= 404) {
        if (s_tap_x < 80) {
            s_running = 0;
            s_state = ST_MENU;
            s_dirty = 1;
            return;
        }
        if (s_tap_x < 158) play = 1;
        else {
            if (s_finished) { s_state = ST_MENU; s_dirty = 1; return; }
            s_running = 0;
            s_dirty = 1;
        }
    }
    if (play) {
        if (s_finished) start_story(s_cur_prompt);
        else { s_paused = !s_paused; s_dirty = 1; }
    }
}

/* ---------------- lifecycle ---------------- */

static void release_arena(void)
{
    if (s_arena) {
        if (HB_APP_AUX_BLOCK == s_arena) HB_APP_AUX_BLOCK = 0;
        hb_os_free(s_arena);
        s_arena = 0;
    }
}

static int load_model(void)
{
    uint8_t head[TLLM_HEADER];
    TllmInfo info;
    uint32_t size, got;
    int rc;

    size = hb_fs_size(MODEL_PATH);
    if (size == 0 && !hb_fs_exists(MODEL_PATH)) return ST_MISSING;
    if (size < TLLM_HEADER) { s_err = TLLM_E_SIZE; return ST_DAMAGED; }
    if (hb_fs_read(MODEL_PATH, head, TLLM_HEADER) != TLLM_HEADER) { s_err = TLLM_E_SIZE; return ST_DAMAGED; }
    rc = tllm_check(head, size, &info);
    if (rc) { s_err = rc; return ST_DAMAGED; }
    s_need = tllm_arena_bytes(&info);
    if (s_largest < s_need + SLOP || s_free < s_need + RESERVE) return ST_LOWMEM;
    s_arena = (uint8_t *)hb_os_alloc(s_need);
    if (!s_arena) return ST_LOWMEM;
    HB_APP_AUX_BLOCK = s_arena;
    got = hb_fs_read_chunked(MODEL_PATH, s_arena, info.file_bytes);
    if (got != info.file_bytes) {
        release_arena();
        s_err = TLLM_E_SIZE;
        return ST_DAMAGED;
    }
    rc = tllm_open(&s_t, s_arena, s_need, info.file_bytes);
    if (rc) {
        release_arena();
        s_err = rc;
        return ST_DAMAGED;
    }
    return ST_MENU;
}

static void draw(void)
{
    switch (s_state) {
    case ST_LOWMEM:  draw_low_memory(); break;
    case ST_MISSING: draw_missing(); break;
    case ST_DAMAGED: draw_damaged(); break;
    case ST_MENU:    draw_menu(); break;
    case ST_KEYS:    draw_keys(); break;
    default:         draw_story(); break;
    }
}

void hb_raw_init(int w, int h)
{
    s_fb = hb_raw_fb();
    s_w = w;
    s_h = h;
    /* A relaunch can find the previous launch's block still held. */
    if (HB_APP_AUX_BLOCK) {
        hb_os_free((void *)HB_APP_AUX_BLOCK);
        HB_APP_AUX_BLOCK = 0;
    }
    s_free = hb_os_heap_free();
    s_largest = hb_os_heap_largest();
    hb_trace_init();
    s_state = load_model();
    hb_trace_log("TLLMIN", s_free, s_largest);
    hb_trace_log("TLLMST", (uint32_t)s_state, s_need);
    s_prev_btn[0] = hb_button_pressed(HB_BTN_VOL_UP);
    s_prev_btn[1] = hb_button_pressed(HB_BTN_VOL_DOWN);
    s_prev_btn[2] = hb_button_pressed(HB_BTN_PLAY_PAUSE);
    s_blink_ms = hb_time_uptime_ms();
    s_dirty = 1;
    draw();
    s_dirty = 0;
}

void hb_raw_frame(const hb_spoint_t *t)
{
    int up, dn, play;
    uint32_t now;
    if (s_state <= ST_DAMAGED) {
        if (s_dirty) { draw(); s_dirty = 0; }
        return;
    }
    read_touch(t);
    up = btn_edge(0, HB_BTN_VOL_UP);
    dn = btn_edge(1, HB_BTN_VOL_DOWN);
    play = btn_edge(2, HB_BTN_PLAY_PAUSE);
    if (s_state == ST_MENU) menu_input(up, dn, play);
    else if (s_state == ST_KEYS) keys_input(play);
    else story_input(up, dn, play);

    if (s_state == ST_STORY) {
        run_engine();
        reveal();
        now = hb_time_uptime_ms();
        if (now - s_blink_ms >= 400u) {
            s_blink_ms = now;
            s_blink = !s_blink;
            s_dirty = 1;
        }
    }
    if (s_dirty) {
        draw();
        s_dirty = 0;
    }
}
