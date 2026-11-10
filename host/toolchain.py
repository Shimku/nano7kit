"""Find an ARM compiler with the newlib headers and multilibs game ports need."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from .compat import is_macos, root

# Ports link ARM-state softfp newlib; the SDK also uses Thumb/soft libgcc.
NEWLIB_FLAGS = ('-marm', '-march=armv5te+fp', '-mfloat-abi=softfp')


def complete(compiler: str) -> bool:
    """Compile real standard headers and check the libraries, without linking a host app."""
    try:
        probe = subprocess.run(
            [compiler, *NEWLIB_FLAGS, '-x', 'c', '-c', '-', '-o', os.devnull],
            input='#include <stdint.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <math.h>\n#include <setjmp.h>\nuint32_t probe(FILE *f) { return sizeof(jmp_buf); }\n',
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        )
        if probe.returncode:
            return False
        for flags, library in ((NEWLIB_FLAGS, 'libc.a'), (NEWLIB_FLAGS, 'libm.a'),
                               (('-mcpu=cortex-a8', '-mthumb', '-mfloat-abi=soft'), 'libgcc.a')):
            result = subprocess.run([compiler, *flags, '-print-file-name=' + library],
                                    text=True, capture_output=True, timeout=10)
            if result.returncode or not Path(result.stdout.strip()).is_file():
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def select() -> str | None:
    """Keep a complete PATH toolchain, otherwise select a complete local installation.

    Update PATH for all child tools (make, ar, objcopy and nested build scripts),
    rather than overriding just the compiler in one game's Makefile.
    """
    current = shutil.which('arm-none-eabi-gcc')
    candidates = [Path(current)] if current else []
    candidates += [root() / 'build/arm-toolchain/bin/arm-none-eabi-gcc',
                   root() / 'build/macos-intel/arm/bin/arm-none-eabi-gcc']
    candidates += sorted((root() / 'build/macos-intel').glob('arm-gnu-toolchain-*/bin/arm-none-eabi-gcc'), reverse=True)
    if is_macos():
        candidates += sorted(Path('/Applications/ArmGNUToolchain').glob('*/arm-none-eabi/bin/arm-none-eabi-gcc'), reverse=True)
    for compiler in dict.fromkeys(candidates):
        if compiler.is_file() and complete(str(compiler)):
            if str(compiler) != current:
                os.environ['PATH'] = str(compiler.parent) + os.pathsep + os.environ.get('PATH', '')
            return str(compiler)
    return None


def ensure(*, check_only: bool = False) -> bool:
    if select():
        return True
    print('    ARM toolchain is missing or incomplete (standard C headers/newlib libraries).')
    if not check_only:
        from .deps import _brew_install, _linux_install
        from .compat import have, is_linux
        if is_macos():
            # Avoid colliding with Homebrew's compiler-only formula and binutils.
            _brew_install('--cask --no-binaries gcc-arm-embedded')
        elif is_linux() and have('apt-get'):
            _linux_install('gcc-arm-none-eabi libnewlib-arm-none-eabi libnewlib-dev')
        if select():
            return True
    print('    Install the complete Arm GNU arm-none-eabi toolchain, including newlib,')
    print('    put its bin directory on PATH, then re-run ./start deps.')
    return False
