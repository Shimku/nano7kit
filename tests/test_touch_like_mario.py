# SPDX-License-Identifier: MIT
"""PocketJS and Fruit Ninja empty the OS touch queues every frame, as Super Mario 64
does (games/sm64/nano-port.patch: hb_touch_drain_all() then hb_touch_poll_multi()).
Mario, the SDK touch code and the launcher are pinned unchanged."""
import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(rel):
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


class TouchLikeMarioTests(unittest.TestCase):
    def test_mario_and_sdk_unchanged(self):
        self.assertEqual(sha("games/sm64/nano-port.patch"),
                         "39938d4061c1ad08444207f3dffa52e5117334f7ea90f729130b631a6467ac86")
        self.assertEqual(sha("nanoapps/sdk/hb_touch.c"),
                         "424daefdf59f316bec37b3a336ae7405ea2f47ed80dd8a63b7c0c199e0b64c3d")
        self.assertEqual(sha("nanoapps/sdk/hb_surface_input.c"),
                         "eb2735a57965c7ccb2c88c857b91e241c43453b6ad78f06640e8ab845073b5ea")

    def test_mario_drains_every_frame(self):
        text = (ROOT / "games/sm64/nano-port.patch").read_text()
        self.assertIn("hb_touch_drain_all();", text)

    def test_pocketjs_drains_every_frame_after_reading(self):
        lines = (ROOT / "pocketjs/nano7g.patch").read_text().split("\n")
        i = next(n for n, l in enumerate(lines)
                 if l.startswith("+    read_queues(now, !touch->down"))
        # touch-snapshot: the launcher's per-event copy is read between the poll and the drain
        self.assertTrue(lines[i + 1].startswith("+    read_snapshot(now, s_present_status, s_present_id);"))
        self.assertTrue(lines[i + 2].startswith("+    hb_touch_drain_all();"))
        # still drains while down before hb_touch_poll_multi, as before
        self.assertEqual(sum(l.strip().startswith("+    hb_touch_drain_all();") or
                             l.strip().startswith("+        hb_touch_drain_all();") for l in lines), 2)
        self.assertIn("@@ -0,0 +1,1695 @@", lines)

    def test_fruitninja_reads_the_queues_only(self):
        """Fruit Ninja never pops a node (no drain). fn-gameplay: the 8-queue diagnostic is
        gone; a list whose newest node is a lift gives the down it holds, then the lift."""
        src = (ROOT / "games/fruitninja/nano/fn_nano.c").read_text()
        self.assertNotIn("hb_touch_drain", src)
        self.assertNotIn("pop_front", src)
        self.assertNotIn("log_queues", src)
        poll = src[src.index("void fn_nano_poll(void)"):]
        poll = poll[:poll.index("\n}\n")]
        self.assertIn("for (int pass = 0; pass < 2; pass++)", poll)
        self.assertIn("if (listed != 1 || !newest_up) break;", poll)
        self.assertIn("*newest_up = *((volatile uint8_t *)(node + 8) + 4) == 1;", src)

if __name__ == "__main__":
    unittest.main()


def test_pocketjs_diag_logs_two_finger_frames_and_kill_reasons():
    """pj-touch-diag: PJTQ/PJTF for the first two-finger frames, PJTKILL/PJTUPV
    when such a stroke ends (flushed soon), PJTSTR when a queue is marked streaming."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "pocketjs" / "nano7g.patch").read_text(encoding="utf-8")
    for tag in ("PJTQ", "PJTF", "PJTKILL", "PJTUPV", "PJTSTR"):
        assert f'"{tag}"' in text, tag
    assert "#define PN_TD_MAX 12u" in text
    assert "s_tk_lines <= 5u" in text and "pocketnano_log_soon();" in text


def test_snapshot_readers_match_the_launcher():
    """touch-snapshot: PocketJS and Fruit Ninja read the launcher's per-event copy of
    every queue's newest sample (hb_touch_snap_t right after hb_shared_t), with the
    launcher's magic; PocketJS no longer learns the lift status and resets its
    tracker for every sub-app run."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    latch = (root / "nanoapps/system/silver_resident_na/touch_latch.h").read_text(encoding="utf-8")
    magic = re.search(r"#define HB_TOUCH_SNAP_MAGIC (0x[0-9a-f]+)u", latch).group(1)
    pj = (root / "pocketjs" / "nano7g.patch").read_text(encoding="utf-8")
    fn = (root / "games/fruitninja/nano/fn_nano.c").read_text(encoding="utf-8")
    assert f"#define PN_SNAP_MAGIC {magic}u" in pj
    assert f"#define FN_SNAP_MAGIC {magic}u" in fn
    for src in (pj, fn):
        assert "(char *)hb_shared + sizeof(hb_shared_t)" in src
        assert "finger[8][4]" in src or "finger[PN_TOUCH_LISTS][4]" in src
    assert "+static int s_up_status = 1;" in pj
    assert '"PJTUP"' not in pj
    poll = pj[pj.index("+static void touch_poll"):]
    poll = poll[:poll.index("\n+}\n")]
    assert poll.index("read_queues(") < poll.index("read_snapshot(") < poll.index("hb_touch_drain_all()")
    assert "+        pn_touch_reset();" in pj and '"PJTSNAP"' in pj and '"PJTSRC"' in pj
    # Fruit Ninja: the snapshot only stands in when the queue poll found nothing
    assert "else if ((listed = snap_touch(&lx, &ly)) != 0) t_from_snap++;" in fn
