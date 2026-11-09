"""PocketJS (MIT): the nano7g target (MicroTS apps as NanoApps), plus the
older first-party 7G HAL hello.

nano7g (2026-10-31): fetch PocketJS at const.POCKETJS_COMMIT, apply
pocketjs/nano7g.patch, AOT compile an app for the 240x432 touch profile,
build the thumbv7a staticlib and link it with the NanoApps RAW_SURFACE shim.
See pocketjs/README.md. The notes below are the 2026-10-29 study.

Read 2026-10-29 from https://github.com/pocket-nexus/pocketjs ,
https://pocketjs.pocket.nexus/docs/cli/ , DEVICE_BUILDS.md, and
apps/ipodnano-hero/pocket.json:

- Licence: MIT (Yifeng "Evan" Wang).
- `pocket compile --target ipodnano --aot` generates Rust sources for
  apps/ipodnano-hero. Viewport 176x132, capabilities text.glyphs.baked
  and input.buttons. That is iPod nano 2G / Rockbox Hero, not this MKN52.
- Device native builds list iPod touch 4/6, iPhone 2G/4S, PSP, Vita,
  3DS, ... not iPod nano 7G RetailOS / Helvetica.
- Changelog: MicroTS Hero as a Rockbox plugin on iPod nano 2G; the
  iPod nano music-player app was removed.
- QuickJS + the Rust core: see `./start pocketjs` (that measures it
  in the emulator). Probe on this MKN52 is 7.0 MB free / 4.0 MB largest
  after a fresh boot.

So: clone for study. Do not vendor. Do not flash. Do not apps add the
PocketJS runtime. The realistic Mac path to a .hbapp is MicroTS AOT
against a first-party 240x432 touch HAL, not the 176x132 ipodnano
target. `./start pocketjs hello` builds that HAL prototype (JS Hello).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import const
from .compat import Refused, have, root, run, which


def checkout() -> Path:
    return root() / "build" / "pocketjs"


def print_status() -> int:
    dest = pinned_checkout()
    present = (dest / ".git").is_dir()
    hello = const.artifact("artifacts/extra/Executables/JS Hello.hbapp")
    hello_n = hello.stat().st_size if hello.is_file() else 0
    print("PocketJS on this MKN52 (nano7g target, Helvetica NanoApps)")
    print("-------------------------------------------------------")
    print("PocketJS is a MIT TypeScript/Rust UI runtime (no DOM, no WebView).")
    print("MicroTS compiles an app's TS model and TSX view to Rust ahead of time;")
    print("nano7kit's nano7g target links that with the PocketJS core and software")
    print("rasterizer into a NanoApps RAW_SURFACE .hbapp (240x432 touch). No JS VM")
    print("runs on the iPod. The QuickJS guest is not used yet.")
    print()
    print("  repo       " + const.POCKETJS_REPO)
    print("  licence    " + const.POCKETJS_LICENSE + " (credited, not vendored)")
    print("  pinned     " + const.POCKETJS_COMMIT + " + pocketjs/nano7g.patch")
    print("  site       " + const.POCKETJS_SITE)
    print("  checkout   " + str(dest) + (" (present)" if present else " (fetched by ./start pocketjs build)"))
    print()
    print_nano7g_apps()
    print(
        "  JS Hello     "
        + (f"{hello_n} bytes" if hello_n else "not staged (./start pocketjs hello)")
        + "; the older pocket_hal.h prototype, not the runtime"
    )
    print()
    print("Upstream --target ipodnano is the nano 2G Rockbox Hero (176x132,")
    print("click wheel buttons). It is not this nano and is not used here.")
    print()
    print("What this toolkit will not do")
    print("  - vendor PocketJS sources (fetched at the pinned commit, not vendored)")
    print("  - flash anything or replace the resident")
    print("  - write NAND (apps add is a Disk Mode copy)")
    print()
    print("exact: ./start pocketjs apps")
    print("       ./start apps add 'JS Hero'")
    print("       ./start pocketjs emu hero")
    print("       ./start pocketjs build hero")
    print("       ./start pocketjs clone     (study checkout, latest upstream)")
    print("       ./start pocketjs hello     (JS Hello HAL)")
    print("docs:  pocketjs/README.md  docs/POCKETJS.md")
    return 0


def clone(*, force: bool = False) -> Path:
    dest = checkout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir() and not force:
        print(f"PocketJS already at {dest}")
        return dest
    if dest.is_dir() and force:
        import shutil

        shutil.rmtree(dest)
    if not have("git"):
        raise Refused("git is required to clone PocketJS")
    print(f"==> cloning PocketJS ({const.POCKETJS_LICENSE}) -> {dest}")
    print("    credit: pocket-nexus / Yifeng Evan Wang. Not vendored.")
    print("    not a nano 7G Helvetica host. Will not be flashed.")
    r = run(
        ["git", "clone", "--depth", "1", const.POCKETJS_REPO, str(dest)],
        check=False,
    )
    if r.returncode != 0 or not dest.is_dir():
        raise Refused(f"git clone PocketJS failed (rc {r.returncode})")
    run(["git", "-C", str(dest), "fetch", "--quiet", "--depth", "1", "origin", const.POCKETJS_COMMIT], check=False)
    if run(["git", "-C", str(dest), "checkout", "--quiet", const.POCKETJS_COMMIT], check=False).returncode != 0:
        raise Refused(f"PocketJS commit {const.POCKETJS_COMMIT[:12]} not found")
    license_file = dest / "LICENSE"
    if license_file.is_file():
        blob = license_file.read_text(encoding="utf-8", errors="replace")[:200]
        if "MIT" not in blob:
            print("    note: LICENSE did not start with MIT; see the file")
    print(f"cloned {dest}")
    print("next: ./start pocketjs hello   (first-party HAL, not the runtime)")
    print("docs: docs/POCKETJS.md  (no flash, no apps add of the runtime)")
    return dest


def build_hello() -> dict:
    """Build the first-party RAW_SURFACE HAL prototype. Host only, no NAND."""
    from .sdkapp import build_app

    app_dir = root() / "nanoapps" / "apps" / "pockethello"
    if not (app_dir / "Makefile").is_file():
        raise Refused("nanoapps/apps/pockethello is missing")
    if not which("arm-none-eabi-gcc"):
        extra = const.artifact("artifacts/extra/Executables/JS Hello.hbapp")
        hbapp = app_dir / "build" / "pockethello.hbapp"
        if extra.is_file():
            print(f"arm-none-eabi-gcc missing; using staged {extra} ({extra.stat().st_size} bytes)")
            print("Disk Mode, one extra per reboot (Do not disconnect):")
            print("  ./start apps add 'JS Hello'")  # two-word Home name
            return {"name": "JS Hello", "hbapp": str(extra), "built": False}
        if hbapp.is_file():
            print(f"arm-none-eabi-gcc missing; using {hbapp}")
            return {"name": "JS Hello", "hbapp": str(hbapp), "built": False}
        raise Refused("arm-none-eabi-gcc is missing (run ./start deps)")
    print("pocketjs hello (first-party HAL, not the PocketJS runtime)")
    print("  viewport  240x432 touch  (nano 7G; upstream ipodnano is 176x132 buttons)")
    print("  slot      A  RAW_SURFACE")
    print("  will not  vendor, flash, or apps add QuickJS")
    built = build_app("pockethello")
    print("honest: HAL prototype compiles on a Mac. AOT of ipodnano-hero is not this binary.")
    return built


# ---------------------------------------------------------------------------
# nano7g: PocketJS MicroTS apps as NanoApps (pocketjs/README.md)
# ---------------------------------------------------------------------------

import json
import os
import shutil
import subprocess
import sys


def pocketjs_dir() -> Path:
    return root() / "pocketjs"


def manifest() -> dict:
    return json.loads((pocketjs_dir() / "apps.json").read_text(encoding="utf-8"))


def pinned_checkout() -> Path:
    return root() / "build" / f"pocketjs-{const.POCKETJS_COMMIT[:10]}"


def out_dir(key: str) -> Path:
    return root() / "build" / "pocketjs-out" / key


def app_entry(token: str) -> dict:
    t = (token or "").strip().lower().replace(" ", "")
    for app in manifest()["apps"]:
        names = {app["key"], app["folder"], app["name"].lower().replace(" ", "")}
        if t in names:
            return app
    keys = ", ".join(a["key"] for a in manifest()["apps"])
    raise Refused(f"unknown PocketJS app {token!r} (known: {keys})")


def _tool_env() -> dict:
    env = dict(os.environ)
    cargo_bin = Path.home() / ".cargo" / "bin"
    env["PATH"] = os.pathsep.join([str(cargo_bin), env.get("PATH", "")])
    return env


def _python_with_elftools() -> str:
    candidates = [sys.executable, str(root() / "build" / "venv" / "bin" / "python"),
                  str(root() / ".venv" / "bin" / "python")]
    for exe in candidates:
        if not exe or not Path(exe).exists():
            continue
        r = subprocess.run([exe, "-c", "import elftools"], capture_output=True)
        if r.returncode == 0:
            return exe
    raise Refused("pyelftools is missing (python3 -m pip install --user pyelftools, or ./start deps)")


def build_tools_missing() -> list:
    env = _tool_env()
    missing = []
    for tool in ("git", "bun", "cargo", "rustup", "arm-none-eabi-gcc", "make"):
        if not shutil.which(tool, path=env["PATH"]):
            missing.append(tool)
    return missing


def _run(cmd, cwd=None, log=None):
    print("    $ " + " ".join(str(c) for c in cmd))
    with (open(log, "a", encoding="utf-8") if log else open(os.devnull, "w")) as fh:
        r = subprocess.run([str(c) for c in cmd], cwd=cwd, env=_tool_env(),
                           stdout=fh if log else None, stderr=subprocess.STDOUT if log else None)
    if r.returncode != 0:
        hint = f"; see {log}" if log else ""
        raise Refused(f"{cmd[0]} failed (rc {r.returncode}){hint}")


def fetch_pinned(log=None) -> Path:
    """Fetch PocketJS at the pinned commit and apply pocketjs/nano7g.patch.

    Host only. The checkout lives under build/ (gitignored); nothing from it
    is committed here."""
    dest = pinned_checkout()
    commit = const.POCKETJS_COMMIT
    patch = pocketjs_dir() / manifest()["patch"]
    if not (dest / ".git").is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        print(f"==> fetching PocketJS ({const.POCKETJS_LICENSE}) at {commit[:10]} -> {dest}")
        _run(["git", "init", "-q"], cwd=dest, log=log)
        _run(["git", "remote", "add", "origin", const.POCKETJS_REPO], cwd=dest, log=log)
        _run(["git", "fetch", "-q", "--depth", "1", "origin", commit], cwd=dest, log=log)
        _run(["git", "-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"], cwd=dest, log=log)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True).stdout.strip()
    if head != commit:
        raise Refused(f"{dest} is at {head[:10]}, not the pinned {commit[:10]}; remove it and rebuild")
    applied = (dest / "hosts" / "nano7g" / "pocket.host.json").is_file()
    if not applied:
        print(f"==> applying {patch.name}")
        _run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=dest, log=log)
    else:
        check = subprocess.run(["git", "apply", "--reverse", "--check", str(patch)], cwd=dest,
                               capture_output=True, text=True)
        if check.returncode != 0:
            raise Refused(f"{dest} has a different nano7g patch applied; remove it and rebuild")
    return dest


def _prepare(dest: Path, log) -> None:
    if not (dest / "node_modules").is_dir():
        _run(["bun", "install", "--frozen-lockfile"], cwd=dest, log=log)
    if not (dest / "platforms" / "web" / "pocketjs.wasm").is_file():
        _run(["rustup", "target", "add", "--toolchain", "1.98.1", "wasm32-unknown-unknown"], cwd=dest, log=log)
        _run(["bun", "tools/wasm.ts"], cwd=dest, log=log)


def quickjs_dir() -> Path:
    return root() / "build" / "pocketjs-quickjs"


def fetch_quickjs(log=None) -> Path:
    """QuickJS at the revision PocketJS pins (pocketjs/apps.json "quickjs"),
    every file checked by sha256, CONFIG_ATOMICS turned off for the iPod (no
    threads). Lands under build/; nothing from it is committed here."""
    from .compat import sha256_file

    q = manifest()["quickjs"]
    repo = quickjs_dir() / "repo"
    if not (repo / ".git").is_dir():
        repo.mkdir(parents=True, exist_ok=True)
        print(f"==> fetching QuickJS {q['version']} ({q['licence']}) at {q['commit'][:10]}")
        _run(["git", "init", "-q"], cwd=repo, log=log)
        _run(["git", "remote", "add", "origin", q["repo"]], cwd=repo, log=log)
    have_commit = subprocess.run(["git", "cat-file", "-e", q["commit"] + "^{commit}"], cwd=repo,
                                 capture_output=True).returncode == 0
    if not have_commit:
        _run(["git", "fetch", "-q", "--depth", "1", "origin", q["commit"]], cwd=repo, log=log)
    _run(["git", "-c", "advice.detachedHead=false", "checkout", "-q", "-f", q["commit"]], cwd=repo, log=log)
    src = repo / q["dir"]
    dest = quickjs_dir() / "src"
    if dest.is_dir():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name, want in q["sha256"].items():
        got = sha256_file(src / name)
        if got != want:
            raise Refused(f"QuickJS {name} sha256 {got[:16]}... is not the pinned {want[:16]}...; nothing was built.")
        shutil.copyfile(src / name, dest / name)
    text = (dest / "quickjs.c").read_text(encoding="utf-8")
    gate = "#if defined(__PSP__) || defined(__vita__)\n#undef CONFIG_ATOMICS"
    if gate not in text:
        raise Refused("QuickJS quickjs.c: the CONFIG_ATOMICS gate moved; nothing was built.")
    (dest / "quickjs.c").write_text(text.replace(
        gate, "#if defined(__PSP__) || defined(__vita__) || defined(__POCKETNANO__)\n#undef CONFIG_ATOMICS"),
        encoding="utf-8")
    return dest


def staged_data_dir() -> Path:
    """Where the built QuickJS packages are staged (committed, like the
    .hbapp files): ./start pocketjs packages copies them from here."""
    return const.artifact("artifacts/extra/Data/PocketJS")


def package_path(key: str) -> Path:
    return staged_data_dir() / manifest()["packages"][key]["file"]


def bytecode_path(key: str) -> Path:
    return staged_data_dir() / (Path(manifest()["packages"][key]["file"]).stem + ".qjsbc")


def data_files(key: str) -> list:
    """(staged path, path under /Apps/Data/PocketJS) for an app's extra
    read-only data: copied files ("data") and baked ones ("bake")."""
    pkg = manifest()["packages"][key]
    return [(staged_data_dir() / d["to"], d["to"]) for d in pkg.get("data", []) + pkg.get("bake", [])]


def package_files(key: str) -> list:
    """The files one QuickJS app reads from /Apps/Data/PocketJS."""
    return [package_path(key), bytecode_path(key)] + [p for p, _ in data_files(key)]


def device_name(path: Path) -> str:
    """A staged file's path relative to /Apps/Data/PocketJS."""
    return path.relative_to(staged_data_dir()).as_posix()


def build_qjsc(dest: Path, qsrc: Path, log=None) -> Path:
    """The host bytecode compiler (programs/nano7g/nanoapps/pocketqjsc.c)
    from the same pinned QuickJS sources as the app."""
    tool = out_dir("packages") / "pocketqjsc"
    tool.parent.mkdir(parents=True, exist_ok=True)
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise Refused("pocketqjsc needs a host C compiler (cc)")
    print("==> host pocketqjsc (QuickJS bytecode compiler)")
    _run([cc, "-O2", "-D_GNU_SOURCE", '-DCONFIG_VERSION="pocketnano"', f"-I{qsrc}",
          dest / "programs" / "nano7g" / "nanoapps" / "pocketqjsc.c",
          *[qsrc / f"{n}.c" for n in ("quickjs", "libregexp", "libunicode", "cutils", "dtoa")],
          "-lm", "-lpthread", "-o", tool], cwd=dest, log=log)
    return tool


def compile_packages(dest: Path, keys, qsrc: Path, log=None) -> list:
    """`pocket compile` (JavaScript, not --aot) for the QuickJS apps, their
    bytecode, both staged under untether/artifacts/extra/Data/PocketJS."""
    out = []
    tool = build_qjsc(dest, qsrc, log)
    staged_data_dir().mkdir(parents=True, exist_ok=True)
    for key in keys:
        pkg = manifest()["packages"][key]
        built = out_dir("packages") / pkg["file"]
        print(f"==> pocket compile {pkg['manifest']} (target nano7g, JavaScript)")
        _run(["bun", "tools/pocket.ts", "compile", "--manifest", pkg["manifest"], "--project-root", ".",
              "--target", "nano7g", "--output", built], cwd=dest, log=log)
        bc = built.with_suffix(".qjsbc")
        _run([tool, built, bc], cwd=dest, log=log)
        shutil.copyfile(built, package_path(key))
        shutil.copyfile(bc, bytecode_path(key))
        for d in pkg.get("data", []):
            target = staged_data_dir() / d["to"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dest / d["from"], target)
        for d in pkg.get("bake", []):
            target = staged_data_dir() / d["to"]
            target.parent.mkdir(parents=True, exist_ok=True)
            print(f"==> bun {d['script']} (read-only data {d['to']})")
            _run(["bun", d["script"], target], cwd=dest, log=log)
        out += package_files(key)
    return out


def build_nano7g_app(token: str) -> dict:
    """AOT compile, preview, Rust staticlib, NanoApps link and stage one app.

    Host only: no NAND, no device. Needs bun, rustup (nightly-2026-07-02 with
    rust-src, 1.98.1 with wasm32-unknown-unknown), arm-none-eabi-gcc."""
    from .app_stage import stage_named
    from .compat import sha256_file

    app = app_entry(token)
    missing = build_tools_missing()
    if missing:
        raise Refused(
            "PocketJS rebuild needs " + ", ".join(missing) + ". The staged "
            f"{app['name']}.hbapp in untether/artifacts/extra already works without them."
        )
    out = out_dir(app["key"])
    out.mkdir(parents=True, exist_ok=True)
    log = out / "build.log"
    log.write_text("", encoding="utf-8")
    dest = fetch_pinned(log)
    _prepare(dest, log)
    aot = out / "aot"
    if aot.is_dir():
        shutil.rmtree(aot)
    samples = manifest()["samples"]
    flags = []
    for key in app["samples"]:
        sample = samples[key]
        print(f"==> pocket compile --aot {sample['manifest']} (target nano7g)")
        _run(["bun", "tools/pocket.ts", "compile", "--manifest", sample["manifest"], "--project-root", ".",
              "--target", "nano7g", "--aot", "--out", aot / key], cwd=dest, log=log)
        flags.append(f"--sample={key}:{aot / key}")
    quickjs = bool(app.get("quickjs"))
    make_extra = []
    packages = []
    if quickjs:
        from .pvz_portable import fetch as fetch_pinned_file

        qsrc = fetch_quickjs(log)
        dl = fetch_pinned_file("dlmalloc", root() / "build" / "pocketjs-quickjs" / "dl")
        packages = compile_packages(dest, app["packages"], qsrc, log)
        flags += ["--quickjs", "--no-preview"]
        make_extra = ["QUICKJS=1", f"QUICKJS_DIR={qsrc}", f"DLMALLOC_C={dl}"]
        print("==> thumbv7a staticlib (QuickJS apps: no host preview)")
    else:
        flags.append(f"--preview-script={pocketjs_dir() / app['script']}")
        print("==> host preview + thumbv7a staticlib")
    _run(["bun", "tools/build/nano7g/build.ts", *flags, f"--outdir={out}"], cwd=dest, log=log)
    lib = out / "target" / "thumbv7a-none-eabi" / "release" / "libpocketjs_nano7g.a"
    app_build = root() / "nanoapps" / "apps" / app["folder"] / "build"
    if app_build.is_dir():
        shutil.rmtree(app_build)
    python = _python_with_elftools()
    print("==> NanoApps link (RAW_SURFACE, relocatable .hbapp)")
    _run(["make", "-C", dest / "programs" / "nano7g" / "nanoapps",
          f"NANOAPPS_SDK={root() / 'nanoapps' / 'sdk'}", f"POCKETNANO_LIB={lib}",
          f"APP_NAME={app['folder']}", f"POCKETNANO_TITLE=\"{app['name']}\"",
          f"BUILD={app_build}", f"PYTHON={python}", *make_extra], log=log)
    elf = app_build / f"{app['folder']}.reloc.elf"
    _run([python, root() / "nanoapps" / "tools" / "check_no_thumb_fp.py", elf], log=log)
    hbapp = app_build / f"{app['folder']}.hbapp"
    asset_hits = _asset_lookalikes(hbapp, out / "generated")
    staged = stage_named(app["folder"], allow_legacy_offsets=asset_hits)
    _record_lookalikes(Path(staged["exe"]), app["name"], asset_hits)
    preview = sorted(str(p) for p in (out / "preview").glob("*.png"))
    record = {
        "app": app["name"], "key": app["key"], "samples": app["samples"], "pocketjs_commit": const.POCKETJS_COMMIT,
        "patch_sha256": sha256_file(pocketjs_dir() / manifest()["patch"]),
        "hbapp": str(hbapp), "hbapp_bytes": hbapp.stat().st_size, "hbapp_sha256": sha256_file(hbapp),
        "library_sha256": sha256_file(lib), "preview": preview,
        "packages": {p.name: {"bytes": p.stat().st_size, "sha256": sha256_file(p)} for p in packages},
    }
    (out / "nano7kit-build.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"  hbapp    {hbapp} ({record['hbapp_bytes']} bytes)")
    if not app.get("quickjs"):
        print(f"  preview  {out / 'preview'}")
    for p in packages:
        print(f"  package  {p} ({p.stat().st_size} bytes)")
    print("Disk Mode, one extra per reboot (slot B):")
    print(f"  ./start apps add '{app['name']}'")
    if packages:
        print("  ./start pocketjs packages     (copies the .pocket files to " + manifest()["packages_dir"] + ")")
    return {"app": app, "staged": staged, "record": record}


def _asset_lookalikes(hbapp: Path, generated: Path) -> list:
    """Legacy-address hits that are bytes of a baked font or image.

    Baked assets are arbitrary data, so a word in them can equal one of the
    old SDK's fixed addresses. A hit counts as asset data only when the 32
    bytes around it occur in one of the generated asset files."""
    from .fixed_addrs import legacy_hits, parse_hbapp

    hits = legacy_hits(hbapp)
    if not hits:
        return []
    image, _ = parse_hbapp(hbapp.read_bytes())
    blobs = [p.read_bytes() for p in generated.rglob("*") if p.suffix in (".bin", ".rgba") and p.is_file()]
    proven = []
    for hit in hits:
        off = hit["offset"]
        window = image[max(0, off - 14):off + 18]
        if any(window in blob for blob in blobs):
            print(f"    legacy-address lookalike at {off:#x} is baked asset data ({hit['what']}); allowed")
            proven.append(off)
    return proven


def _record_lookalikes(exe: Path, name: str, offsets: list) -> None:
    """Keep the asset-lookalike proof for this exact staged file (tracked)."""
    from .app_stage import LOOKALIKES
    from .compat import sha256_file

    rec = root() / LOOKALIKES
    data = json.loads(rec.read_text(encoding="utf-8")) if rec.is_file() else {}
    data = {k: v for k, v in data.items() if v.get("name") != name}
    if offsets:
        data[sha256_file(exe)] = {"name": name, "offsets": sorted(offsets),
                                  "why": "baked font or image bytes equal to an old fixed address"}
    rec.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def emulate(token: str, outdir: Optional[str] = None, script: Optional[str] = None,
            os_blocks: Optional[str] = None, no_bytecode: bool = False) -> int:
    """Run a staged PocketJS .hbapp in the Unicorn dry run (pocketjs/emu.py)."""
    app = app_entry(token)
    hbapp = const.artifact(f"artifacts/extra/Executables/{app['name']}.hbapp")
    if not hbapp.is_file():
        raise Refused(f"{hbapp} is missing (./start pocketjs build {app['key']})")
    out = Path(outdir) if outdir else out_dir(app["key"]) / "emu"
    cmd = [sys.executable, str(pocketjs_dir() / "emu.py"), str(hbapp), str(out),
           str(Path(script) if script else pocketjs_dir() / app.get("emu_script", app["script"]))]
    for key in app.get("packages", []):
        for pkg in package_files(key):
            if not pkg.is_file():
                raise Refused(f"{pkg} is missing (./start pocketjs build {app['key']})")
            if pkg.suffix == ".qjsbc" and no_bytecode:
                continue
            cmd += ["--serve", f"{manifest()['packages_dir']}/{device_name(pkg)}={pkg}"]
    if os_blocks:
        cmd += ["--os-blocks", os_blocks]
    print("==> Unicorn dry run (host only, no device)")
    return subprocess.call(cmd)


def print_nano7g_apps() -> int:
    data = manifest()
    print(f"PocketJS nano7g apps (PocketJS {data['commit'][:10]} + pocketjs/{data['patch']})")
    for app in data["apps"]:
        hbapp = const.artifact(f"artifacts/extra/Executables/{app['name']}.hbapp")
        size = f"{hbapp.stat().st_size} bytes" if hbapp.is_file() else "not staged"
        print(f"  {app['name']:<12} key {app['key']:<8} {size}; {app['from']}")
        for key in app["samples"]:
            sample = data["samples"][key]
            print(f"               - {sample['title']}: {sample['from']}")
        for key in app.get("packages", []):
            pkg = data["packages"][key]
            print(f"               - {pkg['title']}: {pkg['from']} ({data['packages_dir']}/{pkg['file']})")
        print(f"               ./start apps add '{app['name']}'")
        if app.get("packages"):
            print("               ./start pocketjs packages")
    return 0


def add_packages(nano=None, *, skip_wait: bool = False, watch: bool = False, timeout: int = 180) -> dict:
    """Disk Mode FAT copy of the QuickJS packages into /Apps/Data/PocketJS.

    Read-only on the iPod side afterwards: the app only reads them. Same
    durable flush as ./start roms add. Never writes NAND or iPod_Control."""
    from .compat import sha256_file
    from .device import find_nanos, format_nano
    from .fat import MountFat, assert_not_hfs, assert_safe_fat_path, ensure_fat_mount
    from .sync import wait_for_enter_or_watch
    from .untether import check_loader_hashes, durable_flush_and_rehash

    data = manifest()
    fat_dir = data["packages_dir"]
    srcs = [p for k in data["packages"] for p in package_files(k)]
    missing = [str(p) for p in srcs if not p.is_file()]
    if missing:
        raise Refused("package missing: " + ", ".join(missing) + " (./start pocketjs build quickjs)")
    print("nano7kit pocketjs packages")
    print(f"Copy the QuickJS packages into {fat_dir}. Does not write NAND.")
    print("Does not write iPod_Control. Does not wipe /Apps/Startup.")
    print("Prefer Disk Mode (Do not disconnect while plugged in) after Helvetica.")
    print()
    attached = find_nanos()
    for n in attached:
        print(format_nano(n))
        print()
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=timeout)
        print()
    if nano is None:
        found = find_nanos()
        if not found:
            raise Refused("no iPod mass-storage volume. Confirm Disk Mode, then re-run ./start pocketjs packages.")
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    assert_not_hfs(nano)
    root_ = ensure_fat_mount(nano)
    if root_ is None:
        raise Refused("could not mount the FAT volume. Disk Mode, then ./start pocketjs packages. Nothing written.")
    before = check_loader_hashes(nano)
    ops = MountFat(root_)
    ops.mkdir_p(fat_dir)
    for sub in sorted({str(Path(device_name(p)).parent) for p in srcs} - {"."}):
        ops.mkdir_p(f"{fat_dir}/{sub}")
    expected = {}
    wrote = already = 0
    for src in srcs:
        fat = f"{fat_dir}/{device_name(src)}"
        assert_safe_fat_path(fat)
        digest = sha256_file(src)
        status = ops.copy_verified(src, fat, replace=True)
        expected[fat] = digest
        if status == "already":
            already += 1
        else:
            wrote += 1
        print(f"    {status:8}  {fat}  {src.stat().st_size} bytes  sha {digest[:16]}...")
    # the rehash summary prints these counts (a KeyError on
    # 'wrote' ended the run after a good copy on the Mac).
    durable_flush_and_rehash(nano, {"file_hashes": expected, "verified": "yes",
                                    "wrote": str(wrote), "already": str(already)})
    after = check_loader_hashes(nano)
    if before.get("ok") and not after.get("ok"):
        raise Refused("package copy finished but the tested /Apps loader hashes no longer match. "
                      "Re-run ./start install-untether (no firmware).")
    print("POCKETJS_PACKAGES_OK")
    print("    Eject IPOD. Unplug. Sleep/Wake+Home to the Home screen. Open JS QuickJS.")
    return {"ok": True, "hashes": expected}


LOG_TAGS = ("PJOPEN", "PJHEAP", "PJOS", "PJTIGHT", "PJNOMEM", "PJNOPKG", "PJBOOT", "PJLIVE", "PJQJS", "PJGC",
            "PJSTK", "PJPEAK", "PJSPLIT", "PJSLOW", "PJSTEP", "PJBC", "PJFPS", "PJACCXY", "PJACCZ", "PJEND", "PJFREE", "PJFAIL",
            "PJMEMO", "PJMEMP", "PJMEMS", "PJMEMF", "PJMEMA", "PJMEMB", "PJMEMC", "PJMEMT", "PJMEMU", "PJSPARE", "PJFING",
            "PJFSB", "PJFS", "PJFSERR", "PJTOUCH", "PJTRAW", "PJTUP", "PJLOGNO", "PJHOME", "PJRTC", "PJRTC2",
            "PJFAILM", "PJSTKX", "PJLOGW", "PJEXIT", "PJEXITX", "PJMUSIC",
            "PJLEAN", "PJRETRY", "PJSHAPE", "PJNOW", "PJNODES", "PJTEXN", "PJALIGN")

# every app also writes its PJ lines to a text file on the iPod,
# "ms TAG v1 v2" per line, flushed every 5 s and on the way back to the menu
# (every 30 s, and at once after a data.fs save, a heap peak step,
# a failure and on the way out; a failure's message is a "# fail:" line).
# The previous run is kept as <folder>-prev.txt. Unlike lasttrace.bin this
# needs no RetailOS reboot before Disk Mode.
LOG_DIR_FAT = "/Apps/Data/PocketJS/logs"
LOG_DIR_FAT_LEGACY = "/Data/PocketJS/logs"
LOG_STEPS = (
    "How to get a PocketJS log:",
    "  1. Run the app on the iPod and do the test.",
    "  2. Tap the menu key to go back to the PocketJS menu, or just wait 30 s: the app",
    "     writes /Apps/Data/PocketJS/logs/<app>.txt on its own (at once after a save",
    "     or a failure).",
    "  3. Disk Mode: hold Sleep/Wake and Home; as soon as the Apple logo appears,",
    "     switch to Volume Up and Volume Down until it says Do not disconnect.",
    "  4. On the Mac: sudo -E ./start pocketjs log",
    "  The resident's lasttrace.bin is optional extra detail: it only appears if",
    "  the iPod restarted normally to the Home screen once between the",
    "  test and Disk Mode.",
)

# data.fs: the op in the top byte of PJFS / PJFSERR, the error
# detail in PJFSERR's second value (nanofs.c nanofs_error_code).
FS_OPS = {2: "write", 3: "remove", 6: "mkdir", 7: "rename"}
FS_ERRORS = {1: "invalid path", 2: "not found", 3: "quota exceeded", 4: "write failed", 5: "is a directory",
             6: "not a directory", 7: "directory not empty", 8: "remove failed", 9: "rename failed",
             10: "destination exists", 11: "out of memory", 12: "malformed payload", 13: "write too big",
             14: "mkdir failed", 15: "read failed", 16: "fs is not mounted", 99: "other"}

# QuickJS's own breakdown at each peak step (JS_ComputeMemoryUsage): bytes, count.
MEM_PARTS = {"PJMEMO": "objects", "PJMEMP": "properties and shapes", "PJMEMS": "strings and atoms",
             "PJMEMF": "functions", "PJMEMA": "array elements", "PJMEMB": "bytecode objects"}


def summarize_trace(entries) -> list:
    """Paste-able lines from the PocketJS tags of a decoded resident trace:
    entries are (tag, v1, v2). FPS per app, memory, tilt raw values."""
    lines, app, titles, raw = [], None, {}, []
    for tag, v1, v2 in entries:
        if tag not in LOG_TAGS:
            continue
        if tag == "PJBOOT":
            app = v1
            name = titles.get(v1)
            lines.append(f"app #{v1} {name} boot" if name else f"app #{v1} boot")
        elif tag == "PJHEAP":
            lines.append(f"  block {v1 // 1024} KB (largest free before {v2 // 1024} KB)")
        elif tag == "PJOS":
            lines.append(f"  OS keeps {v1 // 1024} KB free, {v2 // 1024} KB largest")
        elif tag == "PJLIVE":
            lines.append(f"  mounted {v1 // 1024} KB, peak {v2 // 1024} KB")
        elif tag == "PJQJS":
            lines.append(f"  QuickJS heap {v1 // 1024} KB, after GC {v2 // 1024} KB")
        elif tag == "PJFPS":
            lines.append(f"  fps {v1 / 10:.1f}, slowest tick {v2 / 1000:.1f} ms")
        elif tag == "PJACCXY":
            sx = v1 - (1 << 32) if v1 & 0x80000000 else v1
            sy = v2 - (1 << 32) if v2 & 0x80000000 else v2
            lines.append(f"  accel raw x {sx} y {sy} ({sx / 65536:.2f} g, {sy / 65536:.2f} g)")
        elif tag == "PJACCZ":
            sz = v1 - (1 << 32) if v1 & 0x80000000 else v1
            lines.append(f"  accel raw z {sz} ({sz / 65536:.2f} g)")
        elif tag == "PJEND":
            fps = v1 * 1000 / v2 if v2 else 0
            lines.append(f"  ran {v1} ticks in {v2 / 1000:.1f} s ({fps:.1f} fps)")
        elif tag == "PJPEAK":
            lines.append(f"  heap peak {v1 // 1024} KB of {v2 // 1024} KB")
        elif tag == "PJSPLIT":
            lines.append(f"    in use then: QuickJS {v1 // 1024} KB, core and files {v2 // 1024} KB")
        elif tag == "PJFSB":
            lines.append(f"  data.fs: {'folder ready' if v1 else 'FOLDER FAILED (no fs for this app)'}, quota {v2 // 1024} KB")
        elif tag == "PJFS":
            op = FS_OPS.get(v1 >> 24, f"op {v1 >> 24}")
            lines.append(f"  data.fs {op} ok" + (f" ({v1 & 0xFFFFFF} bytes)" if v1 >> 24 == 2 else "")
                         + (f" in {v2} ms" if v2 else ""))
        elif tag == "PJFSERR":
            op = FS_OPS.get(v1 >> 24, f"op {v1 >> 24}")
            lines.append(f"  data.fs {op} FAILED: {FS_ERRORS.get(v2, f'error {v2}')} (the app showed it; no reboot)")
        elif tag == "PJTOUCH":
            lists = ",".join(str(i) for i in range(8) if v1 >> i & 1) or "none"
            stats = ",".join(str(i) for i in range(8) if v1 >> (8 + i) & 1) or "none"
            lines.append(f"  touch 10 s: OS lists used {lists}, statuses {stats}, at most {(v1 >> 16) & 0xFF} extra fingers,"
                         f" {v2 & 0xFFFF} samples on lists 1-7, {v2 >> 16} queue-0 ids")
        elif tag == "PJTRAW":
            raw.append((v1, v2))
            if len(raw) <= RAW_SHOWN:
                lines.append(f"    raw touch list {v1 >> 24} status {(v1 >> 16) & 0xFF} id {v1 & 0xFFFF}"
                             f" at {v2 >> 16},{v2 & 0xFFFF}")
        elif tag == "PJTUP":
            downs = ",".join(str(i) for i in range(8) if v2 >> i & 1) or "none"
            lines.append(f"  lift status learned: {v1} (statuses while down: {downs})")
        elif tag == "PJLOGNO":
            lines.append(f"  log file write failed {v1} times; file logging stopped ({v2} bytes kept)")
        elif tag == "PJFING":
            lines.append(f"  {v1} fingers at once (slots {v2:#x})")
        elif tag == "PJMEMC":
            lines.append(f"      bytecode {v1 // 1024} KB, line tables {v2 // 1024} KB")
        elif tag == "PJMEMT":
            lines.append(f"      atoms {v1 // 1024} KB ({v2})")
        elif tag == "PJMEMU":
            lines.append(f"      QuickJS in use {v1 // 1024} KB ({v2} allocations)")
        elif tag == "PJALIGN":
            # The OS block's offset past 16 bytes, and the private stack
            # top's (always 0 now). 4 or 12 on the block is the case
            # where JS QuickJS used to fail with createNode status -2.
            lines.append(f"  block address +{v1} past 16, stack top +{v2}"
                         + ("" if v2 % 8 == 0 else " (STACK TOP NOT 8-BYTE ALIGNED)"))
        elif tag == "PJSPARE":
            lines.append(f"  bundle text reused as heap: {v1 // 1024} KB (space {v2})")
        elif tag in MEM_PARTS:
            lines.append(f"      {MEM_PARTS[tag]} {v1 // 1024} KB ({v2})")
        elif tag == "PJSTK":
            lines.append(f"  private stack used {v1 // 1024} KB of {v2 // 1024} KB")
        elif tag == "PJSTEP":
            what = {1: "read files", 2: "realm", 3: "run bundle"}.get(v1, "step")
            lines.append(f"  boot step {v1} ({what}) {v2 / 1000:.1f} ms")
        elif tag == "PJBC":
            lines.append("  bundle from bytecode" if v1 else "  bundle parsed (no matching .qjsbc)")
        elif tag == "PJSLOW":
            lines.append(f"  slowest tick so far {v1 / 1000:.1f} ms (tick {v2})")
        elif tag in ("PJNOMEM", "PJTIGHT"):
            lines.append(f"  {tag}: {v1 // 1024} KB / {v2 // 1024} KB")
        elif tag == "PJNOPKG":
            lines.append(f"  app #{v1}: package missing (./start pocketjs packages)")
        elif tag == "PJFAIL":
            lines.append(f"  stopped: code {v1} line {v2}")
        elif tag == "PJFAILM":
            lines.append(f"    heap peak then {v1 // 1024} KB of {v2 // 1024} KB")
        elif tag == "PJSTKX":
            lines.append(f"  PRIVATE STACK FULL: {v1 // 1024} KB used of {v2 // 1024} KB (stopped before it hit the heap)")
        elif tag == "PJLOGW":
            lines.append(f"  log file write took {v1 / 1000:.0f} ms ({v2} bytes)")
        elif tag == "PJEXIT":
            lines.append(f"  menu key: the app's exit hook ran in {v1} ms")
        elif tag == "PJEXITX":
            lines.append("  menu key: the app's exit hook threw")
        elif tag == "PJMUSIC":
            what = {1: "pause", 2: "stop", 3: "resume", 0x12: "after stop"}.get(v1, f"op {v1}")
            state = {0: "playing", 1: "paused", 2: "stopped"}.get(v2, "no player")
            lines.append(f"  music {what} (player {state})")
        elif tag == "PJLEAN":
            lines.append(f"  LOW MEMORY (lean mode): got a {v1 // 1024} KB block instead of {v2 // 1024} KB"
                         " (music or Bluetooth holds the big block)")
        elif tag == "PJNOW":
            lines.append(f"  after a page change: heap in use {v1 // 1024} KB (QuickJS {v2 // 1024} KB)")
        elif tag == "PJNODES":
            lines.append(f"      nodes {v2} live ({v1} slots)")
        elif tag == "PJTEXN":
            lines.append(f"      textures {v1}, {v2 // 1024} KB")
        elif tag == "PJSHAPE":
            lines.append(f"      rounded-corner and border masks {v1 // 1024} KB")
        elif tag == "PJRETRY":
            lines.append(f"  home screen stopped while booting: retry {v2} of 2")
        elif tag == "PJFREE":
            lines.append(f"  back to the menu, {v1} bytes left")
        elif tag == "PJHOME":
            name = titles.get(v1, "")
            lines.append(f"  home screen: app #{v1}{' ' + name if name else ''}, {v2} apps in all")
        elif tag == "PJRTC":
            sec, mins, hrs, day = v1 & 0xFF, (v1 >> 8) & 0xFF, (v1 >> 16) & 0xFF, (v1 >> 24) & 0xFF
            month, year = v2 & 0xFF, (v2 >> 16) & 0xFFFF
            lines.append(f"  clock record {v1:08x} {v2:08x}: read as {year}-{month:02d}-{day:02d} {hrs:02d}:{mins:02d}:{sec:02d}")
        elif tag == "PJRTC2":
            lines.append(f"  clock record +8 {v1:08x} +12 {v2:08x}")
        elif tag == "PJOPEN":
            titles = menu_titles(v1)
            lines.append(f"open: {v1 & 0xFF} apps, private stack {'on' if v2 else 'off'}")
    if len(raw) > RAW_SHOWN:
        lists = sorted({v1 >> 24 for v1, _ in raw})
        stats = sorted({(v1 >> 16) & 0xFF for v1, _ in raw})
        ids = sorted({(v1 >> 24, v1 & 0xFFFF) for v1, _ in raw})
        lines.append(f"  raw touch samples {len(raw)} ({RAW_SHOWN} shown): lists {lists}, statuses {stats},"
                     f" {len(ids)} distinct list/id pairs")
    return lines


RAW_SHOWN = 12


def parse_log_text(text: str) -> list:
    """(ms, tag, v1, v2) from a /Apps/Data/PocketJS/logs/*.txt file. Comment
    lines and torn lines (a reboot mid-write) are skipped."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4 or line.startswith("#"):
            continue
        try:
            ms, v1, v2 = int(parts[0]), int(parts[2]), int(parts[3])
        except ValueError:
            continue
        if parts[1].startswith("PJ"):
            out.append((ms, parts[1], v1 & 0xFFFFFFFF, v2 & 0xFFFFFFFF))
    return out


def fail_messages(text: str) -> list:
    """The "# fail: ..." lines a failure writes: the red screen's text."""
    return [line[len("# fail:"):].strip() for line in text.splitlines() if line.startswith("# fail:")]


def summarize_log_text(text: str) -> list:
    rows = parse_log_text(text)
    lines = summarize_trace((tag, v1, v2) for _ms, tag, v1, v2 in rows)
    for message in fail_messages(text):
        lines.append(f"  failure message: {message}")
    if rows:
        lines.append(f"  ({len(rows)} lines over {rows[-1][0] / 1000:.1f} s)")
    return lines


def log_files() -> list:
    """(app name, FAT path, label) for every log file a PocketJS app writes.

    Prefers /Apps/Data/PocketJS/logs; also lists /Data/PocketJS/logs so older
    installs (and hb_data_dir fallback) still show up in ./start pocketjs log.
    """
    out = []
    for app in manifest()["apps"]:
        for root, tag in ((LOG_DIR_FAT, ""), (LOG_DIR_FAT_LEGACY, " legacy /Data")):
            out.append((app["name"], f"{root}/{app['folder']}.txt", "last run" + tag))
            out.append((app["name"], f"{root}/{app['folder']}-prev.txt", "run before" + tag))
    return out


def print_log_steps() -> None:
    for line in LOG_STEPS:
        print(line)


def read_log_files(ops, dest: Optional[Path] = None) -> list:
    """Copy and summarize every PocketJS log file on the volume. Read only.
    Returns [(app, fat_path, label, lines)] for the files present."""
    found = []
    for name, fat, label in log_files():
        data = ops.read_bytes(fat)
        if not data:
            continue
        if dest is not None:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / Path(fat).name).write_bytes(data)
        found.append((name, fat, label, summarize_log_text(data.decode("utf-8", "replace"))))
    return found


def pocketjs_log(file: Optional[str] = None) -> int:
    """./start pocketjs log: the PocketJS log files plus lasttrace.bin."""
    from .apps import _ops_for_nano, decode_lasttrace, dump_lasttrace
    from .fat import assert_not_hfs
    from .state import state_dir

    sections = []
    if file:
        path = Path(file).expanduser()
        data = path.read_bytes()
        if data[:4] == b"RTBH" or path.suffix == ".bin":
            decoded = decode_lasttrace(data)
            sections.append((f"{path.name} (resident trace)",
                             summarize_trace((e["tag"].rstrip(), e["v1"], e["v2"]) for e in decoded.get("entries") or [])))
        else:
            sections.append((path.name, summarize_log_text(data.decode("utf-8", "replace"))))
    else:
        from .device import find_nanos

        found = find_nanos()
        if not found:
            print_log_steps()
            raise Refused("no iPod mass-storage volume. Put the iPod in Disk Mode (steps printed with this message), then re-run "
                          "sudo -E ./start pocketjs log.")
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
        assert_not_hfs(nano)
        ops, _mounted = _ops_for_nano(nano)
        print("nano7kit pocketjs log (read only, no NAND)")
        dest = state_dir() / "pocketjs-logs"
        files = read_log_files(ops, dest)
        print(f"PJLOG_FILES {len(files)} in {LOG_DIR_FAT}" + (f" (copied to {dest})" if files else ""))
        for name, fat, label, lines in files:
            sections.append((f"{name}, {label} ({Path(fat).name})", lines))
        rec = dump_lasttrace(nano, ops=ops, absent_hint=False)
        if rec.get("ok"):
            decoded = decode_lasttrace(Path(rec["path"]).read_bytes())
            sections.append(("lasttrace.bin (resident trace, previous normal boot)",
                             summarize_trace((e["tag"].rstrip(), e["v1"], e["v2"]) for e in decoded.get("entries") or [])))
        elif not rec.get("present"):
            print("    (lasttrace.bin is written only after a panic reboot that leaves DRAM;"
                  " a watchdog leaves no file even after Home. The log files above do not need it.)")
        if not files and not rec.get("ok"):
            print()
            print("PJLOG_ABSENT: no PocketJS log file and no lasttrace.bin on this iPod.")
            print("  No log files yet; reinstall the app if it is an old build.")
            print_log_steps()
            return 1
    print()
    print("POCKETJS_LOG (paste this)")
    for title, lines in sections:
        print(f"== {title}")
        for line in lines or ["no PocketJS entries"]:
            print(line)
    return 0


def menu_titles(count: int) -> dict:
    """Row titles of the library with this many menu rows (the trace carries
    the count, plus bit 8 for the QuickJS library). Rows are
    its MicroTS samples, then its packages."""
    data = manifest()
    found = {}
    quickjs = bool(count & 0x100)
    count &= 0xFF
    for app in data["apps"]:
        if quickjs and not app.get("quickjs"):
            continue
        rows = [data["samples"][k]["title"] for k in app["samples"]]
        listed = rows + [data["packages"][k]["title"] for k in app.get("packages", [])
                         if not data["packages"][k].get("home_only")]
        # the home screen's own apps follow the listed rows (the
        # shim's index order) but are not counted in PJOPEN.
        rows += [data["packages"][k]["title"] for k in app.get("packages", [])]
        if len(listed) == count:
            found = dict(enumerate(rows))
            break
    return found
