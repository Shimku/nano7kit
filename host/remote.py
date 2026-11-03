"""Drive a Raspberry Pi (or any Linux box) over SSH from the Mac.

Setup is once: host, SSH key, remote packages, passwordless sudo for the
agent, and a host-built wInd3x for that Linux arch. After that every
./start command on the Mac does the work; nothing is typed on the Pi.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .compat import Refused, disk_free, have, sha256_file
from .device import Nano, annotate_size
from .safety import check_disk_size
from .state import load_config, remember_backup, repo_root


DEFAULT_REMOTE_ROOT = "~/.local/share/nano7kit"


def ssh_base(host: str) -> list:
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=120",
        "-o", "RequestTTY=no",
        "-o", "LogLevel=ERROR",
        host,
    ]


def ssh_ok(host: str) -> bool:
    r = subprocess.run(ssh_base(host) + ["true"], capture_output=True, text=True)
    return r.returncode == 0


def ssh_run(host: str, remote_cmd: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ssh_base(host) + [remote_cmd],
        check=check,
        capture_output=capture,
        text=True,
    )


def ssh_tty(host: str, remote_cmd: str) -> int:
    """Interactive SSH (sudo password during one-time setup)."""
    return subprocess.call(["ssh", "-t", host, remote_cmd])


def scp_to(host: str, local: Path, remote: str) -> None:
    r = subprocess.run(
        [
            "scp",
            "-q",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8",
            str(local),
            f"{host}:{remote}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise Refused(f"scp to {host} failed: {r.stderr or r.stdout}")


def ensure_ssh_key(host: str) -> None:
    if ssh_ok(host):
        print(f"  ssh {host:<22} ok (key auth)")
        return
    print(f"  ssh {host} is not yet key-authenticated.")
    if not have("ssh-copy-id"):
        raise Refused("ssh-copy-id is missing; install openssh and re-run ./start setup")
    print("  Running ssh-copy-id (you may be asked for the Pi password once)...")
    rc = subprocess.call(["ssh-copy-id", host])
    if rc != 0 or not ssh_ok(host):
        raise Refused(f"could not set up key auth for {host}. Run: ssh-copy-id {host}")


def remote_home(host: str) -> str:
    home = ssh_run(host, "printf %s \"$HOME\"", check=True).stdout.strip()
    if not home.startswith("/"):
        raise Refused(f"could not resolve home directory on {host}")
    return home


def remote_root_for(host: str, home: Optional[str] = None) -> str:
    cfg = load_config()
    saved = cfg.get("REMOTE_ROOT")
    if saved and saved.startswith("/"):
        return saved
    home = home or remote_home(host)
    return f"{home}/.local/share/nano7kit"


def configured_host() -> str:
    cfg = load_config()
    if cfg.get("MODE") == "ssh" and cfg.get("REMOTE"):
        return cfg["REMOTE"]
    raise Refused("no SSH host configured; run ./start setup --remote user@pi")


def configured_remote_root() -> str:
    cfg = load_config()
    saved = cfg.get("REMOTE_ROOT")
    if saved and saved.startswith("/"):
        return saved
    if cfg.get("MODE") == "ssh" and cfg.get("REMOTE"):
        return remote_root_for(cfg["REMOTE"])
    return os.path.expanduser(DEFAULT_REMOTE_ROOT)


def deploy_runtime(host: str, remote_root: str) -> None:
    """Copy host/ plus the hashed untether release files (not the Rust tree)."""
    repo = repo_root()
    print(f"==> deploying nano7kit agent to {host}:{remote_root}")
    ssh_run(host, f"mkdir -p {shlex.quote(remote_root)}")
    tar = subprocess.Popen(
        [
            "tar",
            "-C", str(repo),
            "--exclude", "__pycache__",
            "--exclude", "*.pyc",
            "--exclude", "ipod_sun_untethered",
            "--exclude", "build",
            "--exclude", ".git",
            "-cf", "-",
            "host",
            "untether",
        ],
        stdout=subprocess.PIPE,
    )
    remote = subprocess.Popen(
        ssh_base(host) + [f"tar -C {shlex.quote(remote_root)} -xf -"],
        stdin=tar.stdout,
    )
    if tar.stdout:
        tar.stdout.close()
    remote.communicate()
    tar.wait()
    if remote.returncode != 0 or tar.returncode != 0:
        raise Refused("failed to copy host/ and untether/ to the Pi")
    py = ssh_run(host, "command -v python3", check=False).stdout.strip() or "/usr/bin/python3"
    wrapper = (
        "#!/bin/sh\n"
        f'export NANO7KIT_ROOT={shlex.quote(remote_root)}\n'
        f'export PYTHONPATH={shlex.quote(remote_root)}\n'
        f'exec {shlex.quote(py)} -m host "$@"\n'
    )
    ssh_run(
        host,
        f"mkdir -p {shlex.quote(remote_root + '/bin')} {shlex.quote(remote_root + '/build')} && "
        f"cat > {shlex.quote(remote_root + '/bin/nano7kit')} <<'EOF'\n{wrapper}EOF\n"
        f"chmod +x {shlex.quote(remote_root + '/bin/nano7kit')}",
    )


def install_remote_packages(host: str) -> None:
    print(f"==> installing device packages on {host}")
    cmd = (
        "if command -v apt-get >/dev/null; then "
        "sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "
        "python3 mtools sg3-utils dosfstools libusb-1.0-0-dev pkg-config git make "
        "gcc-arm-none-eabi golang-go openssh-client; "
        "elif command -v dnf >/dev/null; then "
        "sudo dnf install -y python3 mtools sg3_utils dosfstools libusb1-devel git make "
        "arm-none-eabi-gcc-cs golang; "
        "else echo 'no apt/dnf on remote'; exit 1; fi"
    )
    r = ssh_run(host, cmd, check=False)
    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")
    if r.returncode != 0:
        print("warning: remote package install reported errors; ./start doctor will list what is missing")


def install_remote_sudoers(host: str, remote_root: str) -> None:
    """One-time: allow passwordless sudo of the agent only."""
    agent = f"{remote_root}/bin/nano7kit"
    r = ssh_run(host, f"sudo -n {shlex.quote(agent)} doctor >/dev/null 2>&1", check=False)
    if r.returncode == 0:
        print("  remote sudo                 ok (already passwordless for the agent)")
        return
    print("  remote sudo needs a one-time password to install a sudoers drop-in.")
    who = ssh_run(host, "id -un", check=True).stdout.strip()
    body = (
        f"Defaults:{who} !lecture\n"
        f"{who} ALL=(root) NOPASSWD: {agent}\n"
    )
    remote_cmd = (
        f"echo {shlex.quote(body)} | sudo tee /etc/sudoers.d/nano7kit >/dev/null "
        f"&& sudo chmod 440 /etc/sudoers.d/nano7kit "
        f"&& sudo visudo -cf /etc/sudoers.d/nano7kit"
    )
    rc = ssh_tty(host, remote_cmd)
    if rc != 0:
        raise Refused("could not install sudoers drop-in on the Pi")
    r2 = ssh_run(host, f"sudo -n {shlex.quote(agent)} doctor", check=False)
    if r2.returncode != 0:
        print("warning: passwordless sudo for the agent is not confirmed; backup/flash may prompt")
        sys.stderr.write(r2.stderr or "")


def build_remote_helpers(host: str, remote_root: str) -> None:
    """Build wInd3x + usbscsi on the Pi (linux/arm64 flasher is untested)."""
    print("==> building USB helpers on the remote Linux box")
    r = remote_python(host, ["deps"], sudo=False, remote_root=remote_root)
    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")
    if r.returncode != 0:
        print("warning: remote ./start deps reported errors; DFU flash may not be ready until it succeeds")


def remote_python(
    host: str,
    args: list,
    *,
    sudo: bool = True,
    remote_root: Optional[str] = None,
) -> subprocess.CompletedProcess:
    root = remote_root or configured_remote_root()
    agent = f"{root}/bin/nano7kit"
    args = list(args)
    # The Pi must not re-enter SSH mode if a config file was copied. Parent-parser
    # flags belong in front of the subcommand; callers still have to order those.
    if "--local" not in args:
        args.insert(0, "--local")
    quoted = " ".join(shlex.quote(a) for a in args)
    env = f"NANO7KIT_ROOT={shlex.quote(root)} PYTHONPATH={shlex.quote(root)}"
    if sudo:
        inner = f"sudo -n env {env} {shlex.quote(agent)} {quoted}"
    else:
        inner = f"export {env}; {shlex.quote(agent)} {quoted}"
    return ssh_run(host, inner, check=False)


def remote_json(host: str, args: list) -> dict:
    r = remote_python(host, ["--json", *args], sudo=True)
    text = (r.stdout or "").strip()
    if r.returncode != 0:
        raise Refused(f"remote {' '.join(args)} failed:\n{r.stderr or text}")
    try:
        return json.loads(text.splitlines()[-1] if text else "{}")
    except json.JSONDecodeError:
        start = text.find("{")
        if start >= 0:
            return json.loads(text[start:])
        raise Refused(f"remote returned no JSON:\n{text}\n{r.stderr}")


def find_remote(host: str) -> list:
    data = remote_json(host, ["find"])
    nanos = []
    for d in data.get("devices") or []:
        nanos.append(
            Nano(
                os_name="linux-ssh",
                device=d["device"],
                raw_device=d.get("raw_device") or d["device"],
                partition=d["partition"],
                usb_serial=d["usb_serial"],
                size=int(d["size"]),
                fstype=d.get("fstype") or "unknown",
                mountpoints=d.get("mountpoints") or [],
                model=d.get("model") or "iPod",
            )
        )
    return [annotate_size(n) for n in nanos]


def stream_backup(
    host: str,
    nano: Nano,
    dest: Path,
    *,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
) -> dict:
    from .backup import receive_stream

    from .safety import layout_for_disk_size

    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        what="nano",
        layout=layout_for_disk_size(nano.raw_device, nano.size),
    )
    if dest.exists():
        raise Refused(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    free = disk_free(dest.parent)
    if free <= nano.size:
        raise Refused(
            f"not enough free space for a {nano.size} byte image in {dest.parent} "
            f"({free} bytes free). Put the backup on another disk."
        )
    print(f"==> streaming {nano.size} bytes from {host}:{nano.raw_device} -> {dest}")
    inner = (
        f"sudo -n env NANO7KIT_ROOT={shlex.quote(configured_remote_root())} "
        f"PYTHONPATH={shlex.quote(configured_remote_root())} "
        f"{shlex.quote(configured_remote_root() + '/bin/nano7kit')} "
        f"--local backup-stream --device {shlex.quote(nano.device)}"
    )
    proc = subprocess.Popen(
        ssh_base(host) + [inner],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def chunks():
        assert proc.stdout
        while True:
            buf = proc.stdout.read(4 * 1024 * 1024)
            if not buf:
                break
            yield buf

    digest = receive_stream(chunks(), dest, nano.size)
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        dest.unlink(missing_ok=True)
        raise Refused(f"remote backup-stream failed (rc {rc}): {err}")
    (dest.with_suffix(dest.suffix + ".sha256")).write_text(f"{digest}  {dest.name}\n")
    remember_backup(nano.usb_serial, dest, digest, nano.size)
    from .compat import quoted_path

    print(f"Backup complete.\n  --backup {quoted_path(dest)}\n  --backup-sha256 {digest}")
    return {"path": str(dest), "sha256": digest, "size": nano.size, "usb_serial": nano.usb_serial}


def scp_mse_if_needed(host: str, mse: Path, remote_path: str) -> None:
    digest = sha256_file(mse)
    quoted = shlex.quote(remote_path)
    remote_hash = ssh_run(
        host,
        f"if test -f {quoted}; then sha256sum {quoted} | awk '{{print $1}}'; fi",
        check=False,
    ).stdout.strip()
    if remote_hash == digest:
        print(f"    remote firmware image already present ({digest[:16]}...)")
        return
    print(f"==> copying firmware image to {host} ({mse.stat().st_size} bytes)")
    parent = str(Path(remote_path).parent)
    ssh_run(host, f"mkdir -p {shlex.quote(parent)}")
    scp_to(host, mse, remote_path)


def remote_unmount(host: str, nano: Nano) -> None:
    r = remote_python(host, ["unmount", "--device", nano.device], sudo=True)
    sys.stdout.write(r.stdout or "")
    if r.returncode != 0:
        raise Refused(r.stderr or "remote unmount failed")


def remote_fsck(host: str, nano: Nano) -> None:
    r = remote_python(host, ["fsck", "--device", nano.device], sudo=True)
    sys.stdout.write(r.stdout or "")
    if r.returncode != 0:
        raise Refused(r.stderr or "remote fsck failed")


def deploy_dir(host: str, local_dir: Path, remote_dir: str) -> None:
    """Replace remote_dir with the contents of local_dir."""
    local_dir = Path(local_dir)
    if not local_dir.is_dir():
        raise Refused(f"missing directory {local_dir}")
    ssh_run(host, f"rm -rf {shlex.quote(remote_dir)} && mkdir -p {shlex.quote(remote_dir)}")
    tar = subprocess.Popen(
        ["tar", "-C", str(local_dir), "--exclude", "__pycache__", "-cf", "-", "."],
        stdout=subprocess.PIPE,
    )
    remote = subprocess.Popen(
        ssh_base(host) + [f"tar -C {shlex.quote(remote_dir)} -xf -"],
        stdin=tar.stdout,
    )
    if tar.stdout:
        tar.stdout.close()
    remote.communicate()
    tar.wait()
    if remote.returncode != 0 or tar.returncode != 0:
        raise Refused(f"failed to copy {local_dir} to {host}:{remote_dir}")


def setup_remote(host: str) -> dict:
    """One-time: keys, agent, packages, sudoers, USB helpers. Typed only on the Mac."""
    ensure_ssh_key(host)
    home = remote_home(host)
    remote_root = f"{home}/.local/share/nano7kit"
    deploy_runtime(host, remote_root)
    install_remote_packages(host)
    install_remote_sudoers(host, remote_root)
    build_remote_helpers(host, remote_root)
    return {"REMOTE": host, "REMOTE_ROOT": remote_root, "MODE": "ssh"}
