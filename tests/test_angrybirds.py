"""Angry Birds integration tests; no original game files or device required."""
import hashlib
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from host import angrybirds as ab
from host.compat import Refused


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name) / 'AngryBirds.app'
        (self.app / 'data').mkdir(parents=True)
        self.info = {'CFBundleIdentifier': ab.BUNDLE_ID, 'CFBundleVersion': '1.0'}
        self.write_info()
        for name in ('gamelogic.lua', 'blocks.lua', 'starLimits.lua', 'INGAME.dat'):
            (self.app / 'data' / name).write_bytes(name.encode())
        self.digest = ab.asset_digest(self.app / 'data')

    def write_info(self):
        (self.app / 'Info.plist').write_bytes(plistlib.dumps(self.info))

    def test_supported_data_does_not_require_original_executable(self):
        with mock.patch.object(ab, 'ASSET_SHA256', self.digest):
            self.assertTrue(ab.assert_payload(self.app)['ok'])

    def test_modified_data_refused_before_fetch(self):
        with mock.patch('host.ports.fetch_ios_port') as fetch:
            with self.assertRaises(Refused):
                ab.build(self.app)
            fetch.assert_not_called()

    def test_wrong_version_refused(self):
        self.info['CFBundleVersion'] = '1.1'; self.write_info()
        with mock.patch.object(ab, 'ASSET_SHA256', self.digest):
            with self.assertRaises(Refused): ab.assert_payload(self.app)

    def test_wrong_bundle_refused(self):
        self.info['CFBundleIdentifier'] = 'com.other.game'; self.write_info()
        with self.assertRaises(Refused): ab.assert_payload(self.app)

    def test_metadata_does_not_change_fingerprint(self):
        (self.app / 'data/._gamelogic.lua').write_bytes(b'finder metadata')
        self.assertEqual(ab.asset_digest(self.app / 'data'), self.digest)
        (self.app / 'data/gamelogic.lua').write_bytes(b'changed')
        self.assertNotEqual(ab.asset_digest(self.app / 'data'), self.digest)

    def test_plan_requires_supported_payload(self):
        self.assertEqual(ab.plan_lines(None)[-1], 'PLAN_REFUSED')
        with mock.patch.object(ab, 'ASSET_SHA256', self.digest):
            self.assertEqual(ab.plan_lines(self.app)[-1], 'PLAN_OK')


class ManifestTests(unittest.TestCase):
    def test_manifest_checks_sizes_and_no_extra_files(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td); (p/'file.lua').write_bytes(b'ab')
            (p/'files.lst').write_text('file.lua 2\n')
            ab.validate_assets(p)
            (p/'file.lua').write_bytes(b'bad size')
            with self.assertRaises(Refused): ab.validate_assets(p)
            (p/'file.lua').write_bytes(b'ab'); (p/'settings.lua').touch()
            with self.assertRaises(Refused): ab.validate_assets(p)

    def test_unsafe_and_duplicate_names_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td); (p/'file.lua').write_bytes(b'ab')
            for content in ('../file.lua 2\n', 'file.lua 2\nfile.lua 2\n', 'file.lua -1\n'):
                (p/'files.lst').write_text(content)
                with self.assertRaises(Refused): ab.validate_assets(p)


class WiringTests(unittest.TestCase):
    def test_catalog_available_without_fetched_source(self):
        from host.app_catalog import by_name, resolve_name
        from host.ports import NEEDS_PAYLOAD, LARGE_GL, PORT_REPOS, PORT_DATA
        from host.installer import by_name as installer_item
        self.assertEqual(resolve_name('angrybirds'), ab.NAME)
        row = by_name()[ab.NAME]
        self.assertFalse(row['tested'])
        self.assertIn(ab.NAME, NEEDS_PAYLOAD); self.assertIn(ab.NAME, LARGE_GL)
        self.assertEqual(PORT_DATA[ab.NAME]['fat'], '/Apps/Data/AngryBirds')
        self.assertEqual(len(PORT_REPOS['angrybirds']['commit']), 40)
        self.assertFalse(installer_item(ab.NAME).tested)

    def test_add_dispatch_builds_user_payload(self):
        from host import ports
        with mock.patch.object(ab, 'build') as build, mock.patch.object(ports, 'data_ready', return_value=False):
            result = ports.prepare_for_add(ab.NAME, payload=Path('/local/game.ipa'))
        build.assert_called_once_with(Path('/local/game.ipa'))
        self.assertTrue(result['built'])

    def test_cli_plan_dispatch(self):
        from host import cli
        from types import SimpleNamespace
        with mock.patch.object(cli, '_pick_nano', side_effect=Refused('not attached')), \
             mock.patch.object(ab, 'plan_lines', return_value=['Angry Birds plan', 'PLAN_OK']) as plan:
            args = SimpleNamespace(heap_free=None, largest=None)
            self.assertEqual(cli._apps_add_plan(args, ['Angry Birds'], '/local/game.ipa'), 0)
            plan.assert_called_once()

    def test_missing_payload_is_actionable(self):
        from host import ports
        with mock.patch('host.app_catalog.by_name', return_value={ab.NAME: {'present': False}}):
            with self.assertRaisesRegex(Refused, '1.0 assets'):
                ports.prepare_for_add(ab.NAME)

if __name__ == '__main__': unittest.main()
