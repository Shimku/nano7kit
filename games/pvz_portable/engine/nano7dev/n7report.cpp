/* nano7 PvZ dry run: the per-level JSON line the bot writes in the device build (arena and device counters instead
 * of the desktop tracker), plus a screenshot request. Never shipped. */
#include <cstdio>
#include <cstring>
#include <string>
#include "n7dev.h"
#include "n7alloc.h"
extern "C" void n7_dry_request_shot(const char* name);
static uint32_t sTexAtBegin;
static n7_stats_t sAtBegin;
void nano7_dev_level_begin(int lvl)
{
	n7_arena_reset_peak();
	n7_stats.tex_peak = n7_stats.tex_bytes;
	n7_stats.tex_count_peak = n7_stats.tex_count; n7_stats.bake_kept_peak = n7_stats.bake_kept; n7_stats.bake_bytes_peak = n7_stats.bake_bytes;
	n7_stats.os_free_min = 0;
	n7_stats.tris_peak = 0; n7_stats.draws_peak = 0; n7_stats.tris_need_peak = 0; n7_stats.frames_trimmed = 0; n7_stats.frames_capped = 0;
	n7_stats.play_need_peak = n7_stats.play_drawn_peak = n7_stats.play_prot_cut = n7_stats.play_frames = n7_stats.play_frames_dropping = n7_stats.play_drop_max = 0;
	sAtBegin = n7_stats;
	sTexAtBegin = n7_stats.tex_bytes;
	char m[64]; snprintf(m, sizeof m, "nano7 level 1-%d begins", lvl); n7p_log(m);
}
void nano7_dev_report(FILE* out, int lvl, const char* result, int updates, int retries, bool manifest)
{
	n7_arena_stats_t a; n7_arena_stats(&a);
	const n7_stats_t& s = n7_stats;
	fprintf(out, "{\"device\":1,\"level\":%d,\"result\":\"%s\",\"updates\":%d,\"retries\":%d,"
		"\"arena_size\":%zu,\"heap_used\":%zu,\"heap_peak\":%zu,\"heap_high_water\":%zu,\"heap_blocks\":%zu,\"heap_fails\":%zu,\"heap_biggest_fail\":%zu,\"heap_largest_free\":%zu,"
		"\"tex_bytes\":%u,\"tex_peak\":%u,\"tex_count\":%u,\"tex_pieces\":%u,\"tex_fail\":%u,"
		"\"tris_peak\":%u,\"draws_peak\":%u,\"tris_need_peak\":%u,\"tri_budget\":%u,\"frames_trimmed\":%u,\"frames_capped\":%u,"
		"\"img_n7t\":%u,\"img_decoded\":%u,\"img_decoded_bytes\":%u,\"bits_wanted\":%u,\"bits_wanted_bytes\":%u,"
		"\"bits_wanted_level\":%u,\"decoded_level\":%u,"
		"\"file_opens\":%u,\"file_read_kb\":%u,\"file_biggest\":%u,\"snd_played\":%u,\"snd_dropped\":%u,\"upload_buf\":%u,"
		"\"play_need_peak\":%u,\"play_drawn_peak\":%u,\"play_prot_cut\":%u,\"play_frames\":%u,\"play_frames_dropping\":%u,\"play_drop_max\":%u,\"bakes\":%u,"
		"\"tex_count_peak\":%u,\"bake_kept\":%u,\"bake_kept_peak\":%u,\"bake_bytes_peak\":%u,\"bake_refused\":%u,\"bakes_level\":%u,\"os_free_min\":%u,"
		"\"manifest_loaded\":%s}\n",
		lvl, result, updates, retries, a.size, a.used, a.peak, a.high_water, a.blocks, a.fails, a.biggest_fail, n7_arena_largest(),
		s.tex_bytes, s.tex_peak, s.tex_count, s.tex_pieces, s.tex_fail, s.tris_peak, s.draws_peak, s.tris_need_peak, s.tri_budget, s.frames_trimmed, s.frames_capped,
		s.img_n7t, s.img_decoded, s.img_decoded_bytes, s.bits_wanted, s.bits_wanted_bytes,
		s.bits_wanted - sAtBegin.bits_wanted, s.img_decoded - sAtBegin.img_decoded,
		s.file_opens, s.file_read_bytes >> 10, s.file_biggest, s.snd_played, s.snd_dropped, s.upload_buf, s.play_need_peak, s.play_drawn_peak, s.play_prot_cut, s.play_frames, s.play_frames_dropping, s.play_drop_max, s.bakes,
		s.tex_count_peak, s.bake_kept, s.bake_kept_peak, s.bake_bytes_peak, s.bake_refused - sAtBegin.bake_refused, s.bakes - sAtBegin.bakes, s.os_free_min, manifest ? "true" : "false");
	fflush(out);
	char m[200];
	snprintf(m, sizeof m, "nano7 level 1-%d %s: heap peak %.2f MB (high-water %.2f MB of %.2f), textures peak %.2f MB, triangles %u needed at most, %u drawn at most (budget %u)",
		lvl, result, a.peak / 1048576.0, a.high_water / 1048576.0, a.size / 1048576.0, s.tex_peak / 1048576.0, s.tris_need_peak, s.tris_peak, s.tri_budget);
	n7p_log(m); n7p_log_flush();
	char name[32]; snprintf(name, sizeof name, "level1-%d", lvl); n7_dry_request_shot(name);
}
