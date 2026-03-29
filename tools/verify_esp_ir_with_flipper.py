#!/usr/bin/env python3
"""Verify ESP32 B10 IR output by capturing with Flipper CLI `ir rx`.

Flow for each action:
1) Start Flipper IR receive mode.
2) Trigger dashboard command (/api/cmd).
3) Parse decoded lines like: `NECext, A:0x4C4D, C:0x47B8`.
4) Assert expected protocol/address/command observed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import serial


EXPECTED_ADDR = "4C4D"
ACTION_EXPECTED_CMD = {
    "power_toggle": "47B8",
    "fan_speed_step": "45BA",
    "fan_cycle_step": "16E9",
}


@dataclass
class CaptureResult:
    action: str
    ok: bool
    detail: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify ESP IR frames using Flipper receiver.")
    p.add_argument(
        "--flipper-port",
        help="Flipper serial port (auto-detect if omitted), e.g. /dev/cu.usbmodemflip_Avalat1",
    )
    p.add_argument(
        "--dashboard-url",
        default="http://127.0.0.1:8088/api/cmd",
        help="Dashboard command endpoint (default: http://127.0.0.1:8088/api/cmd)",
    )
    p.add_argument(
        "--actions",
        nargs="*",
        default=["power_toggle", "fan_speed_step", "fan_cycle_step"],
        help="Actions to test (default: power_toggle fan_speed_step fan_cycle_step)",
    )
    p.add_argument("--pre-rx-wait-ms", type=int, default=350, help="Wait after `ir rx` starts.")
    p.add_argument(
        "--post-trigger-listen-ms",
        type=int,
        default=2400,
        help="Listen window after triggering command.",
    )
    p.add_argument(
        "--cooldown-ms",
        type=int,
        default=1200,
        help="Delay between actions to avoid overlap.",
    )
    return p.parse_args()


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def find_flipper_port() -> str | None:
    for pattern in ("/dev/cu.usbmodemflip_*", "/dev/tty.usbmodemflip_*"):
        matches = sorted(Path("/dev").glob(pattern.split("/dev/")[1]))
        if matches:
            return str(matches[0])
    return None


def read_for(ser: serial.Serial, seconds: float) -> str:
    end = time.time() + seconds
    out = []
    while time.time() < end:
        b = ser.read(ser.in_waiting or 1)
        if b:
            out.append(b)
    return strip_ansi((b"".join(out)).decode("utf-8", errors="replace"))


def trigger_dashboard_action(dashboard_url: str, action: str) -> tuple[bool, str]:
    payload = json.dumps({"action": action}).encode("utf-8")
    req = urllib.request.Request(
        dashboard_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status == 200, body
    except urllib.error.URLError as exc:
        return False, f"dashboard_error:{exc}"


def extract_nec_lines(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = re.search(r"NECext,\s*A:0x([0-9A-Fa-f]+),\s*C:0x([0-9A-Fa-f]+)", line)
        if m:
            out.append((m.group(1).upper(), m.group(2).upper()))
    return out


def capture_action(
    ser: serial.Serial,
    dashboard_url: str,
    action: str,
    pre_rx_wait_ms: int,
    post_trigger_listen_ms: int,
) -> CaptureResult:
    expected_cmd = ACTION_EXPECTED_CMD[action]

    ser.reset_input_buffer()
    ser.write(b"ir rx\r\n")
    _ = read_for(ser, pre_rx_wait_ms / 1000.0)

    ok, dash_detail = trigger_dashboard_action(dashboard_url, action)
    if not ok:
        ser.write(b"\x03")
        _ = read_for(ser, 0.4)
        return CaptureResult(action=action, ok=False, detail=dash_detail)

    rx_text = read_for(ser, post_trigger_listen_ms / 1000.0)
    ser.write(b"\x03")
    rx_text += read_for(ser, 0.4)

    decodes = extract_nec_lines(rx_text)
    if not decodes:
        return CaptureResult(action=action, ok=False, detail="no NECext decode lines")

    got_unique = sorted({f"A:{a} C:{c}" for a, c in decodes})
    for addr, cmd in decodes:
        if addr == EXPECTED_ADDR and cmd == expected_cmd:
            return CaptureResult(
                action=action,
                ok=True,
                detail=f"matched A:{addr} C:{cmd} (seen={len(decodes)})",
            )

    return CaptureResult(
        action=action,
        ok=False,
        detail=f"expected A:{EXPECTED_ADDR} C:{expected_cmd}; got {got_unique}",
    )


def main() -> int:
    args = parse_args()

    bad_actions = [a for a in args.actions if a not in ACTION_EXPECTED_CMD]
    if bad_actions:
        print(f"Unknown actions: {bad_actions}")
        return 2

    flipper_port = args.flipper_port or find_flipper_port()
    if not flipper_port:
        print("FAIL no Flipper port found")
        return 2

    print(f"Flipper port: {flipper_port}")
    print(f"Dashboard URL: {args.dashboard_url}")

    results: list[CaptureResult] = []
    try:
        ser = serial.Serial(flipper_port, 115200, timeout=0.1)
    except Exception as exc:
        print(f"FAIL cannot open Flipper port: {exc}")
        return 2

    with ser:
        time.sleep(0.2)
        _ = read_for(ser, 0.2)  # Drain prompt/banner.
        for idx, action in enumerate(args.actions):
            if idx > 0:
                time.sleep(args.cooldown_ms / 1000.0)
            res = capture_action(
                ser=ser,
                dashboard_url=args.dashboard_url,
                action=action,
                pre_rx_wait_ms=args.pre_rx_wait_ms,
                post_trigger_listen_ms=args.post_trigger_listen_ms,
            )
            results.append(res)
            status = "PASS" if res.ok else "FAIL"
            print(f"{status} {action}: {res.detail}")

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"RESULT: FAIL ({len(failed)}/{len(results)} failed)")
        return 1

    print(f"RESULT: PASS ({len(results)} actions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
