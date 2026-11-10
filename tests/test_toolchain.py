"""Regression checks for compiler-only installations and shared tool selection."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from host import toolchain


class ToolchainTests(unittest.TestCase):
    def test_missing_headers_rejected(self):
        with patch.object(toolchain.subprocess, 'run', return_value=Mock(returncode=1)):
            self.assertFalse(toolchain.complete('/compiler'))

    def test_compiler_only_library_name_rejected(self):
        results = [Mock(returncode=0), Mock(returncode=0, stdout='libc.a\n')]
        with patch.object(toolchain.subprocess, 'run', side_effect=results):
            self.assertFalse(toolchain.complete('/compiler'))

    def test_real_libraries_required_for_both_abis(self):
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td)/'lib.a'; lib.touch()
            results = [Mock(returncode=0)] + [Mock(returncode=0, stdout=str(lib)) for _ in range(3)]
            with patch.object(toolchain.subprocess, 'run', side_effect=results) as run:
                self.assertTrue(toolchain.complete('/compiler'))
                self.assertIn('-mfloat-abi=softfp', run.call_args_list[1].args[0])
                self.assertIn('-mthumb', run.call_args_list[3].args[0])

    def test_fallback_updates_path_for_all_child_tools(self):
        with tempfile.TemporaryDirectory() as td:
            compiler = Path(td)/'build/arm-toolchain/bin/arm-none-eabi-gcc'
            compiler.parent.mkdir(parents=True); compiler.touch()
            with patch.object(toolchain, 'root', return_value=Path(td)), \
                 patch.object(toolchain.shutil, 'which', return_value=None), \
                 patch.object(toolchain, 'is_macos', return_value=False), \
                 patch.object(toolchain, 'complete', return_value=True), \
                 patch.dict(os.environ, {'PATH':'/original'}):
                self.assertEqual(toolchain.select(), str(compiler))
                self.assertEqual(os.environ['PATH'], str(compiler.parent)+os.pathsep+'/original')

    def test_check_only_does_not_install(self):
        with patch.object(toolchain, 'select', return_value=None), \
             patch('host.deps._brew_install') as install, patch('host.deps._linux_install') as linux:
            self.assertFalse(toolchain.ensure(check_only=True))
            install.assert_not_called(); linux.assert_not_called()

    def test_macos_install_avoids_existing_binary_conflicts(self):
        with patch.object(toolchain, 'select', side_effect=[None, '/complete/bin/arm-none-eabi-gcc']), \
             patch.object(toolchain, 'is_macos', return_value=True), \
             patch('host.deps._brew_install', return_value=True) as install:
            self.assertTrue(toolchain.ensure())
            install.assert_called_once_with('--cask --no-binaries gcc-arm-embedded')

if __name__ == '__main__':
    unittest.main()
