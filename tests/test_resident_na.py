"""Host-only checks for the NanoApps heavy resident install path.

unittest (tests/run.py) style, so CI runs these. rc1 had pytest-only
functions here, which tests/run.py never collected.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from host import const, resident_na
from host.compat import Refused
from host.ports import SLOT_B_FAT, SLOT_B_MARKER

ROOT = Path(__file__).resolve().parents[1]
RC1_NA_SHA = "3426a7a80a729cce28c49fd175ef5efd9e2ef9f13f7a8c3717704e5e859458cf"
ORIGINAL_B = b"original resident-B bytes"


class FakeOps:
    """Just enough of the FAT ops for install-na / restore."""

    def __init__(self, files):
        self.files = dict(files)

    def exists(self, p):
        return p in self.files

    def read_bytes(self, p):
        return self.files.get(p)

    def copy_verified(self, src, dst, replace=False):
        data = Path(src).read_bytes()
        if self.files.get(dst) == data:
            return "already"
        self.files[dst] = data
        return "wrote"

    def delete(self, p):
        self.files.pop(p, None)


def _run(fn, ops, bdir):
    with mock.patch.object(resident_na, "_open_nano", return_value=object()), \
         mock.patch.object(resident_na, "_ops", return_value=(ops, None)), \
         mock.patch.object(resident_na, "backup_dir", return_value=bdir), \
         mock.patch("host.untether.durable_flush_and_rehash"), \
         mock.patch("builtins.print"):
        bdir.mkdir(parents=True, exist_ok=True)
        return fn(backup=Path("/nonexistent"), backup_sha256="a" * 64, skip_wait=True)


class ResidentNaArtifactTests(unittest.TestCase):
    def test_na_resident_artifact_present(self):
        path = resident_na.na_resident_path()
        self.assertTrue(path.is_file())
        self.assertEqual(len(resident_na.na_sha256()), 64)
        self.assertGreater(path.stat().st_size, 1000)

    def test_status_prints_ok(self):
        with mock.patch("builtins.print") as p:
            self.assertEqual(resident_na.status(), 0)
        out = "\n".join(" ".join(map(str, c.args)) for c in p.call_args_list)
        self.assertIn("na image", out)
        self.assertTrue("install-na" in out or "backup" in out)

    def test_paint_and_freeram_packages_staged(self):
        for folder in (ROOT / "untether/artifacts/extra/Apps/Paint.nanoapp",
                       ROOT / "untether/artifacts/extra/Apps/Free RAM.nanoapp"):
            self.assertTrue(folder.is_dir(), folder)
            for leaf in ("manifest.napp", "app.hbapp", "icon.bin"):
                self.assertTrue((folder / leaf).is_file(), folder / leaf)

    def test_na_image_reads_nano7kit_pack_paths(self):
        """rc1 read only /Library/AllApps.pack, which a nano7kit iPod does not
        have, so Home showed the .nanoapp folders and none of the pack apps."""
        data = resident_na.na_resident_path().read_bytes()
        for needle in (b"/Apps/AllApps-B.pack\0", b"/Apps/AllApps.pack\0",
                       b"/Apps/Startup/lasttrace.bin\0", b"/Apps/Executables/\0"):
            self.assertIn(needle, data)
        self.assertNotIn(b"/Library/lasttrace.bin", data)

    def test_na_image_hash_is_pinned(self):
        self.assertEqual(resident_na.na_sha256(), const.NA_B_SHA256)
        self.assertIn(RC1_NA_SHA, const.NA_B_OLD_SHA256S)
        self.assertNotIn(const.NA_B_SHA256, const.NA_B_OLD_SHA256S)
        self.assertEqual(const.NA_B_ALL_SHA256S,
                         const.NA_B_OLD_SHA256S | {const.NA_B_SHA256})

    def test_free_ram_nanoapp_shares_pack_identity(self):
        """Free RAM dedupes by SBID: the .nanoapp must say hb.freeram."""
        data = (ROOT / "untether/artifacts/extra/Apps/Free RAM.nanoapp/manifest.napp").read_bytes()
        bundle = data[36:]
        sbid_off = int.from_bytes(bundle[36:40], "little")
        self.assertEqual(bundle[sbid_off:].split(b"\0", 1)[0], b"hb.freeram")


class ResidentNaRc3Tests(unittest.TestCase):
    def test_rc2_image_is_a_known_upgrade_source(self):
        rc2 = "641a0ac7d43c389cefa42235430b1141a275883b0c12a9f73a22b4ba9eb808ef"
        self.assertIn(rc2, const.NA_B_OLD_SHA256S)
        self.assertNotEqual(const.NA_B_SHA256, rc2)

    def test_rc3_image_is_a_known_upgrade_source(self):
        rc3 = "b9101b96a2b0785a856418c6cb564dfa876cbc2d9ca913071b91f8f98acc8b2c"
        self.assertIn(rc3, const.NA_B_OLD_SHA256S)
        self.assertNotEqual(const.NA_B_SHA256, rc3)


class ResidentNaInstallTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.bdir = Path(td.name) / "na-backup"

    def test_install_over_rc1_keeps_original_backup(self):
        ops = FakeOps({SLOT_B_FAT: ORIGINAL_B, SLOT_B_MARKER: b""})
        _run(resident_na.install_na, ops, self.bdir)
        orig_sha = hashlib.sha256(ORIGINAL_B).hexdigest()
        meta = json.loads((self.bdir / resident_na.BACKUP_META).read_text())
        self.assertEqual(meta["previous_sha256"], orig_sha)
        # The rc1 image is on the iPod; install rc2 on top without a restore.
        old = hashlib.sha256(b"rc1 image").hexdigest()
        ops.files[SLOT_B_FAT] = b"rc1 image"
        with mock.patch.object(const, "NA_B_ALL_SHA256S",
                               frozenset({const.NA_B_SHA256, old})):
            out = _run(resident_na.install_na, ops, self.bdir)
        meta = json.loads((self.bdir / resident_na.BACKUP_META).read_text())
        self.assertEqual(meta["previous_sha256"], orig_sha)
        self.assertEqual(meta["upgraded_from"], old)
        self.assertEqual((self.bdir / resident_na.BACKUP_B).read_bytes(), ORIGINAL_B)
        self.assertEqual(out["installed_sha256"], const.NA_B_SHA256)
        self.assertEqual(ops.files[SLOT_B_FAT], resident_na.na_resident_path().read_bytes())
        _run(resident_na.restore, ops, self.bdir)
        self.assertEqual(ops.files[SLOT_B_FAT], ORIGINAL_B)
        self.assertIn(SLOT_B_MARKER, ops.files)

    def test_real_rc1_hash_takes_upgrade_path(self):
        ops = FakeOps({SLOT_B_FAT: ORIGINAL_B, SLOT_B_MARKER: b""})
        _run(resident_na.install_na, ops, self.bdir)
        ops.files[SLOT_B_FAT] = b"rc1 image"
        real = hashlib.sha256
        with mock.patch.object(resident_na.hashlib, "sha256",
                               side_effect=lambda b=b"": mock.Mock(hexdigest=lambda: RC1_NA_SHA)
                               if b == b"rc1 image" else real(b)):
            _run(resident_na.install_na, ops, self.bdir)
        self.assertEqual((self.bdir / resident_na.BACKUP_B).read_bytes(), ORIGINAL_B)

    def test_reinstall_same_na_keeps_original_backup(self):
        ops = FakeOps({SLOT_B_FAT: ORIGINAL_B, SLOT_B_MARKER: b""})
        _run(resident_na.install_na, ops, self.bdir)
        _run(resident_na.install_na, ops, self.bdir)
        self.assertEqual((self.bdir / resident_na.BACKUP_B).read_bytes(), ORIGINAL_B)
        _run(resident_na.restore, ops, self.bdir)
        self.assertEqual(ops.files[SLOT_B_FAT], ORIGINAL_B)

    def test_install_after_restore_backs_up_original_again(self):
        ops = FakeOps({SLOT_B_FAT: ORIGINAL_B, SLOT_B_MARKER: b""})
        _run(resident_na.install_na, ops, self.bdir)
        _run(resident_na.restore, ops, self.bdir)
        _run(resident_na.install_na, ops, self.bdir)
        self.assertEqual((self.bdir / resident_na.BACKUP_B).read_bytes(), ORIGINAL_B)

    def test_install_over_na_without_backup_refuses(self):
        na = resident_na.na_resident_path().read_bytes()
        ops = FakeOps({SLOT_B_FAT: na})
        with self.assertRaises(Refused):
            _run(resident_na.install_na, ops, self.bdir)
        self.assertEqual(ops.files[SLOT_B_FAT], na)


if __name__ == "__main__":
    unittest.main()
