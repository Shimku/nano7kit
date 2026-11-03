# SPDX-License-Identifier: MIT
"""Give files that a sudo run created inside the repo back to the user.

`sudo -E ./start ...` runs as root, so build outputs, untether/artifacts and
__pycache__ it writes inside the checkout end up root-owned, and a later
`git merge` as the user fails on them. After every ./start run under sudo,
anything in the repo still owned by root goes back to SUDO_UID:SUDO_GID.
Only root-owned entries are touched; symlinks are not followed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional


def sudo_ids(environ=None, euid: Optional[int] = None):
    """(uid, gid) of the sudo caller, or None when not root via sudo."""
    env = os.environ if environ is None else environ
    if (os.geteuid() if euid is None else euid) != 0:
        return None
    try:
        uid, gid = int(env.get("SUDO_UID", "")), int(env.get("SUDO_GID", ""))
    except ValueError:
        return None
    if uid == 0:
        return None
    return uid, gid


def restore_owner(repo: Path, uid: int, gid: int, *, lchown: Callable = os.lchown,
                  lstat: Callable = os.lstat) -> int:
    """lchown every root-owned entry under repo (repo included) to uid:gid."""
    fixed = 0

    def fix(path: str) -> None:
        nonlocal fixed
        try:
            st = lstat(path)
        except OSError:
            return
        if st.st_uid != 0:
            return
        try:
            lchown(path, uid, gid)
            fixed += 1
        except OSError:
            pass

    fix(str(repo))
    for dirpath, dirnames, filenames in os.walk(repo, followlinks=False):
        for name in dirnames + filenames:
            fix(os.path.join(dirpath, name))
    return fixed


def restore_repo_owner(repo: Path, environ=None, euid: Optional[int] = None) -> int:
    ids = sudo_ids(environ, euid)
    if ids is None:
        return 0
    try:
        return restore_owner(repo, *ids)
    except Exception:  # never turn a finished command into a failure
        return 0
