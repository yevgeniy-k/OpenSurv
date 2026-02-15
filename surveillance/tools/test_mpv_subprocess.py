"""Utility to launch mpv via subprocess independently of the full OpenSurv stack.

The script mirrors Stream.start_stream() launch flags closely enough to reproduce
Windows-specific issues (process exit codes, watchdog behaviour, etc.).
"""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
from typing import List


def build_command(args: argparse.Namespace) -> List[str]:
    """Assemble the mpv command comparable to Stream.start_stream."""

    command = [
        args.mpv_path,
        f"--video-aspect-override={args.video_aspect}",
        f"--title={args.title}",
        "--no-border",
        f"--video-rotate={args.video_rotate}",
        "--window-minimized=yes",
        "--no-input-default-bindings",
        "--no-input-builtin-bindings",
        "--no-osc",
        "--cursor-autohide=always",
        f"--screen={args.screen}",
        f"--geometry={args.geometry}",
        "--no-audio",
    ]

    if args.extra:
        # Match Stream.start_stream by letting users paste freeform mpv options.
        command.extend(shlex.split(args.extra, posix=os.name != "nt"))

    command.append(args.url)
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Stream URL to feed mpv (e.g. srt://...)" )
    parser.add_argument("--mpv-path", default="mpv", help="Path to mpv executable")
    parser.add_argument("--geometry", default="960x540+0+0", help="mpv geometry string")
    parser.add_argument("--screen", default="0", help="Screen index to pass to mpv")
    parser.add_argument("--video-aspect", default="16:9", help="Aspect ratio hint")
    parser.add_argument("--video-rotate", default="0", help="Video rotation in degrees")
    parser.add_argument("--title", default="opensurv-mpv-test", help="Window title")
    parser.add_argument(
        "--extra",
        default="",
        help="Freeform mpv options (identical to freeform_advanced_mpv_options)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0,
        help="Optional wait timeout in seconds (0 waits indefinitely)",
    )

    args = parser.parse_args()

    command = build_command(args)
    print("Launching mpv with:")
    for token in command:
        print(f"  {token}")

    env = os.environ.copy()
    env.setdefault("DISPLAY", "0")

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

    try:
        process = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.PIPE,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        print(f"Failed to exec mpv: {exc}")
        return 127

    print(f"Spawned mpv PID {process.pid}")

    try:
        if args.timeout > 0:
            returncode = process.wait(timeout=args.timeout)
        else:
            returncode = process.wait()
    except subprocess.TimeoutExpired:
        print("Timeout hit; sending Ctrl+C to mpv process group")
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        returncode = process.wait()

    print(f"mpv exited with code {returncode}")
    return returncode


if __name__ == "__main__":
    sys.exit(main())
