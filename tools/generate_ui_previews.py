#!/usr/bin/env python3
"""Generate monochrome preview screenshots for README documentation.

These are generated UI previews (not device captures).
Output files:
  screenshots/preview_dashboard.png
  screenshots/preview_auto_on.png
  screenshots/preview_controls.png
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


W, H = 128, 64
BG = 0
FG = 255


def render(lines: list[str], out_path: Path) -> None:
    image = Image.new("L", (W, H), BG)
    draw = ImageDraw.Draw(image)
    y = 2
    for line in lines:
        draw.text((2, y), line, fill=FG)
        y += 10
    image.save(out_path)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    render(
        [
            "One Sixteenth Sleep",
            "Temp: 19.72 C",
            "Sensor: OK (PA7)",
            "Target: 20.0C Auto:OFF",
            "Pwr:ON Spd:1 Cyc:1",
            "Last: BOOT",
        ],
        out_dir / "preview_dashboard.png",
    )

    render(
        [
            "One Sixteenth Sleep",
            "Temp: 22.45 C",
            "Sensor: OK (PA7)",
            "Target: 20.0C Auto:ON",
            "Pwr:ON Spd:3 Cyc:3",
            "Last: AUTO_SPD",
        ],
        out_dir / "preview_auto_on.png",
    )

    render(
        [
            "Controls",
            "OK: Power toggle",
            "LEFT: Fan speed step",
            "RIGHT: Fan cycle step",
            "UP/DOWN: Target +/- 0.5C",
            "LONG OK: Auto toggle",
        ],
        out_dir / "preview_controls.png",
    )

    print(f"Wrote previews to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
