"""The resident's touch mailbox latch (nanoapps/system/silver_resident_na/touch_latch.h).

NanoApps' resident latched the FRONT (oldest) sample of the OS first-finger
queue. With a backlog, an old lift in front of a new press read as up while the
finger was down. These run the real header on the host against a fake queue.
unittest style, so tests/run.py collects them.
"""
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from host import const

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "nanoapps" / "system" / "silver_resident_na"
ARTIFACT = ROOT / "untether" / "artifacts" / "extra" / "Startup" / "resident-B-na.hbapp"

HARNESS = r"""
#include <stdio.h>
#include <string.h>
#define HB_TL_RAM(a) ((a) != 0)
#include "touch_latch.h"

/* A node like the OS's: next, prev, then the sample (id, status byte, x, y). */
typedef struct { hb_tl_word next, prev; int32_t id; uint8_t st, pad[3]; int32_t x, y; } node_t;
static hb_tl_word head[6];
static node_t sent, nodes[20];

static void queue(int n, const int *st, const int *xy) {
    memset(nodes, 0, sizeof nodes);
    hb_tl_word prev = (hb_tl_word)&sent;
    sent.next = sent.prev = (hb_tl_word)&sent;
    for (int i = 0; i < n; i++) {
        nodes[i].st = (uint8_t)st[i]; nodes[i].x = xy[2 * i]; nodes[i].y = xy[2 * i + 1];
        nodes[i].prev = prev; ((node_t *)prev)->next = (hb_tl_word)&nodes[i];
        nodes[i].next = (hb_tl_word)&sent; sent.prev = (hb_tl_word)&nodes[i];
        prev = (hb_tl_word)&nodes[i];
    }
    head[4] = (hb_tl_word)&sent; head[5] = (hb_tl_word)n;
}
static void run(const char *name) {
    int32_t x = -1, y = -1, p = -1;
    int r = hb_tl_pick(head, &x, &y, &p);
    printf("%s %d %d %d %d\n", name, r, p, x, y);
}
static void run_newest(const char *name) {
    int32_t o[4] = {-1, -1, -1, -1};
    hb_tl_newest(head, o);
    printf("%s %d %d %d %d\n", name, (int)o[0], (int)o[1], (int)o[2], (int)o[3]);
}
int main(void) {
    { int st[] = {0}; int xy[] = {10, 20}; queue(1, st, xy); run("one_down"); }
    { int st[] = {1}; int xy[] = {10, 20}; queue(1, st, xy); run("one_up"); }
    { int st[] = {1, 0}; int xy[] = {10, 20, 200, 300}; queue(2, st, xy); run("lift_then_press"); }
    { int st[] = {0, 2, 2}; int xy[] = {1, 1, 2, 2, 3, 3}; queue(3, st, xy); run("drag_backlog"); }
    { int st[] = {0, 1}; int xy[] = {50, 60, 51, 61}; queue(2, st, xy); run("tap_press_lift"); }
    { int st[] = {1, 1}; int xy[] = {5, 6, 7, 8}; queue(2, st, xy); run("all_lifted"); }
    { queue(0, 0, 0); run("empty"); }
    { queue(0, 0, 0); head[5] = 3; run("count_but_no_nodes"); }
    { int st[] = {0}; int xy[] = {1, 2}; queue(1, st, xy); head[4] = 0; run("bad_sentinel"); }
    /* PocketJS / Mario style reads on 485bef2 (pinned, see Pin485Tests) */
    { int st[] = {0, 2, 2, 2}; int xy[] = {1, 1, 2, 2, 3, 3, 4, 4}; queue(4, st, xy); run("streaming_hold"); }
    { int st[] = {2, 1}; int xy[] = {9, 9, 10, 10}; queue(2, st, xy); run("move_then_lift"); }
    { int st[] = {1, 0, 2}; int xy[] = {5, 5, 6, 6, 7, 7}; queue(3, st, xy); run("lift_land_move"); }
    { int st[20], xy[40];
      for (int i = 0; i < 20; i++) { st[i] = (i < 16) ? 1 : 0; xy[2 * i] = i; xy[2 * i + 1] = i; }
      queue(20, st, xy); run("bounded_walk"); }
    /* touch snapshot (hb_tl_newest): the newest sample, untouched queue */
    { int st[] = {0, 2, 2}; int xy[] = {1, 1, 2, 2, 3, 3}; queue(3, st, xy); nodes[2].id = 7; run_newest("snap_drag"); }
    { int st[] = {2, 1}; int xy[] = {9, 9, 10, 10}; queue(2, st, xy); run_newest("snap_lift"); }
    { queue(0, 0, 0); run_newest("snap_empty"); }
    { int st[] = {0}; int xy[] = {1, 2}; queue(1, st, xy); head[4] = 0; run_newest("snap_bad_sentinel"); }
    { int st[] = {2, 2}; int xy[] = {-3, 4, -5, 6}; queue(2, st, xy); run_newest("snap_negative"); }
    return 0;
}
"""


class TouchLatchTests(unittest.TestCase):
    def _harness_output(self):
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cc:
            self.skipTest("no host C compiler")
        with tempfile.TemporaryDirectory() as t:
            c = Path(t) / "t.c"
            c.write_text(HARNESS)
            exe = Path(t) / "t"
            subprocess.run([cc, "-O1", "-Wall", "-Werror", "-I", str(RES), "-o", str(exe), str(c)], check=True)
            out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
        return {line.split()[0]: tuple(int(v) for v in line.split()[1:]) for line in out.splitlines()}

    def test_latch_rules(self):
        got = self._harness_output()
        self.assertEqual(got["one_down"], (1, 1, 10, 20))
        self.assertEqual(got["one_up"], (1, 0, 10, 20))
        # The regression: the front (old) sample is a lift, the finger is down again.
        self.assertEqual(got["lift_then_press"], (1, 1, 200, 300))
        # A backlog of a drag: the newest place, not the oldest.
        self.assertEqual(got["drag_backlog"], (1, 1, 3, 3))
        # A whole tap queued at once still latches down first (front-sample behaviour kept).
        self.assertEqual(got["tap_press_lift"], (1, 1, 50, 60))
        self.assertEqual(got["all_lifted"], (1, 0, 7, 8))
        self.assertEqual(got["empty"][0], 0)
        self.assertEqual(got["count_but_no_nodes"][0], -1)
        self.assertEqual(got["bad_sentinel"][0], -1)
        # At most 16 nodes are read: the 4 down ones past them are not seen.
        self.assertEqual(got["bounded_walk"], (1, 0, 15, 15))

    def test_snapshot_newest(self):
        got = self._harness_output()
        self.assertEqual(got["snap_drag"], (7, 2, 3, 3))      # id, status, x, y of the newest node
        self.assertEqual(got["snap_lift"], (0, 1, 10, 10))    # a lift is passed on as status 1
        self.assertEqual(got["snap_empty"][1], 0xff)
        self.assertEqual(got["snap_bad_sentinel"][1], 0xff)
        self.assertEqual(got["snap_negative"], (0, 2, -5, 6))

    def test_hook_snapshot_after_unchanged_mailbox(self):
        src = (RES / "silver_resident.c").read_text()
        hook = src[src.index("static int hb_posttouch_hook"):]
        hook = hook[:hook.index("\n}\n")]
        latch = hook.index("hb_tl_pick((volatile hb_tl_word *)TOUCH_LIST_HEAD")
        snap = hook.index("hb_tl_newest(")
        tramp = hook.index("TRAMP(s_posttouch_tramp)")
        self.assertLess(latch, snap)       # the mailbox is written first, exactly as 485bef2
        self.assertLess(snap, tramp)       # and the snapshot before the OS drains the queues
        self.assertIn("s_shared_x.snap.seq++", hook)
        self.assertIn("_Static_assert(__builtin_offsetof(__typeof__(s_shared_x), snap) == sizeof(hb_shared_t)", src)

    def test_resident_uses_the_latch(self):
        src = (RES / "silver_resident.c").read_text()
        self.assertIn('#include "touch_latch.h"', src)
        hook = src[src.index("static int hb_posttouch_hook"):]
        hook = hook[:hook.index("\n}\n")]
        self.assertIn("hb_tl_pick((volatile hb_tl_word *)TOUCH_LIST_HEAD", hook)
        self.assertNotIn("first + 8", hook)          # the old front-sample read is gone
        self.assertIn("TRAMP(s_posttouch_tramp)", hook)

    def test_artifact_is_the_pinned_build(self):
        self.assertEqual(hashlib.sha256(ARTIFACT.read_bytes()).hexdigest(), const.NA_B_SHA256)
        self.assertIn("4ee7737084fd178e71148026ae30272394d396c21014532ca8e543e426463282",
                      const.NA_B_OLD_SHA256S)


class Pin485Tests(unittest.TestCase):
    """Launcher 485bef2 is what made PocketJS pinch and two fingers work on the iPod
    (3 Nov 2026). Pin it: any launcher touch change must fail here first."""

    def test_launcher_bytes_pinned(self):
        self.assertEqual(const.NA_B_SHA256,
                         "aad1664b06ab055acd67eef854a6107fe6da6874c059844c783f0d0706577866")  # 485bef2 latch + touch snapshot
        self.assertIn("9f1c8e8a0f9a8f800509935405cf42299d2333131d633bac64cc3d79565a5a4b", const.NA_B_OLD_SHA256S)
        self.assertEqual(hashlib.sha256((RES / "touch_latch.h").read_bytes()).hexdigest(),
                         "26340a7c223c33b4747ff52be9f3b6361eaf7a27a6f381bc386be47a501ec200")
        self.assertEqual(hashlib.sha256((RES / "silver_resident.c").read_bytes()).hexdigest(),
                         "3be4cd6975b93dffbe5eed62f686d8094f84d215a502581ebb72d047a7e40b93")

    def test_multi_finger_reads_pinned(self):
        out = TouchLatchTests._harness_output(self)
        self.assertEqual(out["streaming_hold"], (1, 1, 4, 4))   # held finger: down, newest place
        self.assertEqual(out["move_then_lift"], (1, 1, 9, 9))   # 485bef2: down at the last move
        self.assertEqual(out["lift_land_move"], (1, 1, 7, 7))   # new press behind an old lift


if __name__ == "__main__":
    unittest.main()
