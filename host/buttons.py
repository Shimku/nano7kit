# SPDX-License-Identifier: MIT
"""Button combos for the iPod nano 7G, in the wording verified on hardware.

Sleep/Wake is the button on top. Home is the round button under the screen.
Volume Up and Volume Down are on the left side.
"""

from __future__ import annotations

from typing import List

DFU = (
    "Hold Sleep/Wake (top) and Home together.",
    "The nano turns off, the Apple logo appears, then the screen goes black.",
    "Keep holding about one more second (long enough to say \"nano\").",
    "Let go of both buttons. The screen stays black: that is recovery (DFU) mode.",
)

DISK_MODE = (
    "Hold Sleep/Wake (top) and Home together.",
    "As soon as the Apple logo appears, quickly switch to holding Volume Up and Volume Down.",
    "Keep holding them until the screen says \"Do not disconnect\".",
)

RESTART = (
    "Hold Sleep/Wake (top) and Home together until the Apple logo appears.",
    "Let go and wait for the Home screen.",
)

TITLES = {
    "dfu": "Put the nano in recovery (DFU) mode:",
    "disk": "Put the nano in Disk Mode:",
    "restart": "Restart the nano:",
}

STEPS = {"dfu": DFU, "disk": DISK_MODE, "restart": RESTART}


def lines(kind: str, indent: str = "  ") -> List[str]:
    out = [TITLES[kind]]
    for i, step in enumerate(STEPS[kind], 1):
        out.append(f"{indent}{i}. {step}")
    return out


def text(kind: str, indent: str = "  ") -> str:
    return "\n".join(lines(kind, indent))


def show(kind: str, indent: str = "  ") -> None:
    print(text(kind, indent))
