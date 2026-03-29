#!/usr/bin/env python3
"""Deterministic Flipper app input harness with optional ESP IR validation.

This script can:
1) Open the app on Flipper.
2) Send a sequence of button events through CLI `input send`.
3) Optionally listen to ESP serial (IRRX lines) and assert expected B10 commands appear.
"""

from __future__ import annotations

import argparse
import re
import threading
import time
from pathlib import Path

import serial


APP_PATH_DEFAULT = "/ext/apps/GPIO/one_sixteenth_sleep_flipper.fap"

ACTION_TO_INPUT = {
    "ok": ("ok", "short"),
    "left": ("left", "short"),
    "right": ("right", "short"),
    "up": ("up", "short"),
    "down": ("down", "short"),
    "back": ("back", "short"),
    "long_ok": ("ok", "long"),
}

# Actions that are expected to emit IR.
ACTION_TO_EXPECTED_CMD = {
    "ok": "47B8",  # power
    "left": "45BA",  # fan speed
    "right": "16E9",  # fan cycle
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Drive Flipper app inputs and optionally validate IR on ESP.")
    p.add_argument("--flipper-port", help="Flipper serial port (auto-detect if omitted).")
    p.add_argument(
        "--esp-port",
        help="ESP serial port for IRRX validation (optional). If omitted, IR validation is skipped.",
    )
    p.add_argument(
        "--app-path",
        default=APP_PATH_DEFAULT,
        help=f"Flipper app path (default: {APP_PATH_DEFAULT})",
    )
    p.add_argument(
        "--sequence",
        default="ok,left,right,up,down,long_ok",
        help="Comma-separated action sequence. Supported: ok,left,right,up,down,back,long_ok",
    )
    p.add_argument(
        "--between-ms",
        type=int,
        default=300,
        help="Delay between input events in milliseconds.",
    )
    p.add_argument(
        "--strict-ir",
        action="store_true",
        help="Fail when expected IR commands are not observed on ESP (requires --esp-port).",
    )
    return p.parse_args()


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def autodetect_flipper_port() -> str | None:
    for pattern in ("cu.usbmodemflip_*", "tty.usbmodemflip_*"):
        matches = sorted(Path("/dev").glob(pattern))
        if matches:
            return str(matches[0])
    return None


def send_cli(ser: serial.Serial, command: str, wait_s: float = 0.35) -> str:
    ser.write((command + "\r\n").encode("utf-8"))
    end = time.time() + wait_s
    out = []
    while time.time() < end:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            out.append(chunk)
    return strip_ansi((b"".join(out)).decode("utf-8", errors="replace"))


def input_send(ser: serial.Serial, key: str, event_type: str) -> None:
    _ = send_cli(ser, f"input send {key} {event_type}", wait_s=0.18)


def main() -> int:
    args = parse_args()
    actions = [a.strip() for a in args.sequence.split(",") if a.strip()]
    unknown = [a for a in actions if a not in ACTION_TO_INPUT]
    if unknown:
        print(f"Unknown actions: {unknown}")
        return 2

    flipper_port = args.flipper_port or autodetect_flipper_port()
    if not flipper_port:
        print("FAIL: Flipper serial port not found")
        return 2

    esp_ser: serial.Serial | None = None
    esp_lines: list[str] = []
    stop_esp = threading.Event()

    def esp_reader() -> None:
        assert esp_ser is not None
        while not stop_esp.is_set():
            raw = esp_ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                esp_lines.append(line)

    print(f"Flipper port: {flipper_port}")
    if args.esp_port:
        print(f"ESP port: {args.esp_port}")

    try:
        with serial.Serial(flipper_port, 115200, timeout=0.1) as flipper:
            time.sleep(0.25)
            flipper.reset_input_buffer()
            _ = send_cli(flipper, "loader close", wait_s=0.25)
            launch_out = send_cli(flipper, f'loader open "{args.app_path}"', wait_s=0.45)
            print(f"Launch response: {launch_out.strip() or '<empty>'}")

            esp_thread: threading.Thread | None = None
            if args.esp_port:
                esp_ser = serial.Serial(args.esp_port, 115200, timeout=0.1)
                time.sleep(0.4)
                esp_ser.reset_input_buffer()
                esp_thread = threading.Thread(target=esp_reader, daemon=True)
                esp_thread.start()

            try:
                for action in actions:
                    key, evt = ACTION_TO_INPUT[action]
                    input_send(flipper, key, evt)
                    print(f"SENT action={action} ({key},{evt})")
                    time.sleep(args.between_ms / 1000.0)
            finally:
                _ = send_cli(flipper, "loader close", wait_s=0.25)
                if esp_ser:
                    stop_esp.set()
                    if esp_thread:
                        esp_thread.join(timeout=1.0)
                    esp_ser.close()
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 2

    if not args.esp_port:
        print("DONE: inputs sent (IR validation skipped: no --esp-port)")
        return 0

    # Validate by checking whether expected command hex appears in any IRRX line.
    ir_lines = [ln for ln in esp_lines if ln.startswith("IRRX,")]
    print(f"ESP lines captured: total={len(esp_lines)} irrx={len(ir_lines)}")
    if ir_lines:
        print("Sample IRRX lines:")
        for ln in ir_lines[:6]:
            print(f"  {ln}")

    missing: list[str] = []
    upper_lines = [ln.upper() for ln in ir_lines]
    for action in actions:
        expected = ACTION_TO_EXPECTED_CMD.get(action)
        if not expected:
            continue
        if not any(expected in ln for ln in upper_lines):
            missing.append(f"{action}:{expected}")

    if missing:
        msg = f"IR_VALIDATE_FAIL missing={missing}"
        if args.strict_ir:
            print(msg)
            return 1
        print(msg + " (non-strict)")
    else:
        print("IR_VALIDATE_PASS all expected command hex values observed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
