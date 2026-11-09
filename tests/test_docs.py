#!/usr/bin/env python3
"""Public docs: no em dashes, no private strings, links resolve, credits present."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EM = "\u2014"
OWN = [ROOT / "README.md", ROOT / "NOTICE", ROOT / "CREDITS.md", ROOT / "install",
       ROOT / "UPSTREAM.txt", ROOT / "start"]
OWN_DIRS = [ROOT / "host", ROOT / "docs", ROOT / "tests", ROOT / "scripts", ROOT / "pocketjs"]
PUBLIC = ["README.md", "CREDITS.md", "NOTICE", "pocketjs/README.md"] + [
    str(p.relative_to(ROOT)) for p in sorted((ROOT / "docs").glob("*.md"))]


class NoEmDashTests(unittest.TestCase):
    def test_owned_files(self):
        files = list(OWN)
        for folder in OWN_DIRS:
            for path in folder.rglob("*"):
                if path.suffix in {".py", ".md", ".go", ".sh", ".yml", ".txt", ".c", ".html", ".patch", ".json"} and path.is_file():
                    files.append(path)
        bad = [str(p.relative_to(ROOT)) for p in files if EM in p.read_text(encoding="utf-8", errors="replace")]
        self.assertEqual(bad, [], f"em dash (U+2014) in {bad}")


class ReleaseDocsTests(unittest.TestCase):
    def test_readme_is_short_and_has_the_one_liner(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 250)
        self.assertIn("git clone https://github.com/shimku/nano7kit.git && cd nano7kit && ./install", text)
        for needle in ("NanoApps 1.0", "nfzerox", "olievans123", "Mid-2015", "Intel", "Apple silicon",
                       "restore-tested", "CREDITS.md", "PocketJS", "6ca9f914", "LGPL-3.0", "OFL", "MIT",
                       "log.txt", "watchdog"):
            self.assertIn(needle, text)

    def test_no_claims(self):
        for rel in PUBLIC:
            text = (ROOT / rel).read_text(encoding="utf-8").lower()
            for word in ("the first", "world's first", "most complete", "ultimate", "milestone", "tonight"):
                self.assertNotIn(word, text, rel)
            self.assertIsNone(re.search(r"pocketjs\d+|\brc\d\b", text), rel)

    def test_doc_links_resolve(self):
        for rel in PUBLIC:
            path = ROOT / rel
            for target in re.findall(r"\]\(([^)#]+)\)", path.read_text(encoding="utf-8")):
                if "://" in target:
                    continue
                self.assertTrue((path.parent / target).exists(), f"{rel}: {target}")

    def test_docs_named_by_code_exist(self):
        for path in list((ROOT / "host").glob("*.py")) + [ROOT / "start", ROOT / "install"]:
            for rel in re.findall(r"docs/[A-Za-z0-9_-]+\.md", path.read_text(encoding="utf-8")):
                self.assertTrue((ROOT / rel).is_file(), f"{path.name}: {rel}")

    def test_credits(self):
        text = (ROOT / "CREDITS.md").read_text(encoding="utf-8")
        for needle in ("wInd3x", "ipod_sun", "NanoApps", "nano7-untethered", "olievans123",
                       "PvZ-Portable", "LGPL", "PocketJS", "6ca9f914", "MIT", "yui540", "Inter", "OFL"):
            self.assertIn(needle, text)
        for port in ("Mario 64", "Mario Kart 64", "Game Boy", "Temple Run", "Flappy Bird", "Doodle Jump", "none stated"):
            self.assertIn(port, text)

    def test_licence_is_mit_with_lgpl_pvz(self):
        lic = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", lic)
        self.assertIn("Copyright (c) 2026", lic)
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("games/pvz_portable/          LGPL-3.0-or-later", notice)
        self.assertIn("LGPL-3.0-or-later", (ROOT / "games" / "pvz_portable" / "LICENSE").read_text(encoding="utf-8"))

    def test_no_private_strings(self):
        """No owner names, device serials, private commit ids or build paths. Needles are hashed or split."""
        import hashlib
        import re
        import subprocess

        hashed = {
            "8458b1d651d9faf2691730497b34526730947b758078610ec3a56ebe844fd1a3",
            "569d2b7e4bb4534f308741d232a9bc51dd766e150ff4e19fd151a78ea82d6f64",
            "f7e3735cb50aef44eafb4d949e25d6476f5ce0e86c44fa6c5f4e97f39b5297d2",
            # private-history commit ids (they do not exist in the public repo)
            "f2b1ce3e2275d11c9a8a1f2084618a9824c0da13f5e83b2d1c1d6373099ed47b",
            "6e2a31cb78b2736f42f88763e82f2e6f760cb0e4d50b9b2050fe4984a1e67769",
            "43bfeac70084a37202d0be78650a4df843d3bf39d47898b1dbd7ceb33907465b",
            "ccd2d7e523ce460f25e94e2afd7d209cf06f64a08a6094a559cc391824a61cb5",
            "94c035aa1d6909ddf49bea073278de6fe41719c6e1ccfa50a431d6ea2d138fe5",
            "36d9f26cd05f45d19b56db91ab6350516b6b04d5e3f978cfd74d8df0ec6301c1",
            "bb5aaa590079680dc52686a14503403e840bc3a5d13848792bf8ef9a6c185c74",
            "0b394f4d5b5b587f9997ba1dd94cf94123236987d294ce32dd1d85720ca33acc",
            "5f580497dde3415468e2cdae60053f04ed77bbd615b85556891817c5643e2def",
            "3d2979bc14cd555ae9c69784ce9180d153970605dccd1a7ae631cd982264af1f",
            "b2f897ba795cf55d967aa5adcc360a93265bccb21a8aa6224aa3413955eded54",
            "840446c762b2bf944871577e0bd2f7e5a2fbe593b6a3cb306f9c6f87dbffd4f0",
            "3b68b54e25e79c391b2c5effd8559f10a96f7a8b4b94a21387bf485a15e132e6",
            "56a1412142a7379561925d560840dc2f3975b23e808d0aa9022408e6d1516ef3",
        }
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
        bad = []
        for rel in files:
            path = ROOT / rel
            if not path.is_file():
                continue
            data = path.read_bytes()
            text = data.decode("utf-8", errors="replace")
            for word in set(re.findall(r"[A-Za-z0-9]{6,}", text)):
                if hashlib.sha256(word.lower().encode()).hexdigest() in hashed and rel != "LICENSE":
                    bad.append(rel)  # LICENSE names the copyright holder on purpose
            if b"/Users/" in data and b"/Users/you" not in data and path.suffix in {".md", ".py", ".txt", ".sh"}:
                bad.append(rel + " (/Users/)")
            if b"/work" + b"space/" in data or b"/home/" + b"box/" in data:
                bad.append(rel + " (build path)")
            if b"/cursor/" + b"stores" in data:
                bad.append(rel + " (agent store path)")
        self.assertEqual(sorted(set(bad)), [])

    def test_buttons_doc_matches_engine(self):
        from host import buttons

        text = (ROOT / "docs" / "BUTTONS.md").read_text(encoding="utf-8")
        for step in buttons.DFU + buttons.DISK_MODE + buttons.RESTART:
            self.assertIn(step, text)


if __name__ == "__main__":
    unittest.main()
