# Watchdog Subprocess Lifecycle (2026-01-22)

## Objective & Scope
- Addresses "Next Research Thread #1" from 2026-01-21 overview: document how watchdog subprocesses spawn and are supervised per stream for troubleshooting crews.
- Focuses on RTSP/HTTP/video streams rendered through `mpv`; image placeholders reuse the same orchestration but swap the child binary.

## Call Chain Overview
1. `surveillance.py` loads `advanced.interval_check_status` (default 19s) and, every interval ticks, calls `ScreenManager.run_watchdogs_active_screen()` for each monitor manager ([surveillance/surveillance.py#L189-L247](surveillance/surveillance.py#L189-L247)).
2. `ScreenManager` forwards the request only to the currently visible screen, so cached screens are not polled ([surveillance/core/ScreenManager.py#L232-L240](surveillance/core/ScreenManager.py#L232-L240)).
3. `Screen.run_screen_watchdogs()` iterates over the screen's `connectable_streams`—the subset that passed probing—and delegates to each `Stream` ([surveillance/core/Screen.py#L64-L118](surveillance/core/Screen.py#L64-L118)).
4. `Stream.run_stream_watchdog()` inspects its `mpv` (or image viewer) child process handle, restarting it when the process has exited or went missing ([surveillance/core/Stream.py#L313-L423](surveillance/core/Stream.py#L313-L423)).

Implications:
- Only streams that were deemed connectable and belong to the on-screen layout receive watchdog attention.
- Cached/hidden screens rely on their initial startup state; they will only be healed once rotated into the active slot.

## Spawning Child Processes
### Command Assembly
- Non-image streams build an `mpv` command with geometry, rotation, OSC/input suppression, screen targeting, and user-configured extras ([surveillance/core/Stream.py#L336-L414](surveillance/core/Stream.py#L336-L414)).
- Image streams use `core/util/image_viewer.py` with equivalent coordinate arguments so the watchdog logic can stay identical ([surveillance/core/Stream.py#L358-L375](surveillance/core/Stream.py#L358-L375)).
- `freeform_advanced_mpv_options`, audio toggles, forced coordinates, and fullscreen placement flags originate from `etc/monitorN.yml` and per-stream overrides ([surveillance/core/Stream.py#L23-L118](surveillance/core/Stream.py#L23-L118)).

### Process Groups & Environment
- Each launch copies the current environment, injecting the correct `DISPLAY` so X11 tools control the new window even when multiple displays are present ([surveillance/core/Stream.py#L382-L391](surveillance/core/Stream.py#L382-L391)).
- Linux: `preexec_fn=os.setsid` starts `mpv` in a fresh process group so later `os.killpg` calls reap every descendant (overlay filters, hardware decoders, etc.).
- Windows: `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` provides the same isolation, because `os.setsid` is unavailable.
- The `Popen` handle is saved as `self.streamprocess`; its PID is logged immediately for cross-checking against `ps`/Task Manager ([surveillance/core/Stream.py#L396-L408](surveillance/core/Stream.py#L396-L408)).

## Monitoring & Recovery
1. **Watchdog loop** – `Stream.run_stream_watchdog()` calls `poll()` on the child. If it returns a code, OpenSurv logs the failure, sends a newline on stdin (helps mpv exit cleanly when it is hung waiting for input), and hands control to `restart_stream()` ([surveillance/core/Stream.py#L313-L333](surveillance/core/Stream.py#L313-L333)).
2. **Restart path** – `restart_stream()` is a convenience wrapper: `stop_stream()` tears down the old process, then `start_stream()` reconstructs windows and mpv parameters ([surveillance/core/Stream.py#L419-L423](surveillance/core/Stream.py#L419-L423)).
3. **Termination semantics** – `stop_stream()` targets the entire process group via `os.killpg(..., SIGKILL)` on Unix or `Popen.terminate()` on Windows, then waits for completion so PID reuse does not confuse future polls ([surveillance/core/Stream.py#L423-L438](surveillance/core/Stream.py#L423-L438)).
4. **Screen-level bookkeeping** – When a screen recalculates its layout, it stops every previously connectable stream before re-launching survivors, ensuring stale handles do not linger ([surveillance/core/Screen.py#L120-L194](surveillance/core/Screen.py#L120-L194)).
5. **Scheduler cadence** – With the default 19-second interval (configurable in `etc/general.yml`), each active stream is probed roughly every 19 seconds per monitor. Dual-monitor setups therefore run two independent watchdog sweeps each interval ([surveillance/etc/general.yml#L1-L26](surveillance/etc/general.yml#L1-L26)).

## Troubleshooting Playbook
- **Capture the launch command**: set `freeform_advanced_mpv_options: "--log-file=... --msg-level=all=v"` in the stream definition to mirror the exact flags handed to mpv ([surveillance/etc/monitor1.yml#L1-L30](surveillance/etc/monitor1.yml#L1-L30)). The command logged at `start_stream` confirms geometry, rotation, and mpv path.
- **Verify PID ownership**: match the PID printed at spawn time with system tools to ensure the process group is intact. On Linux, `ps -o pgid= -p <pid>` should equal the PID; if not, `os.killpg` cannot reap descendants.
- **Check watchdog cadence**: if a stream is not restarting, ensure `interval_check_status` is not set excessively high and that the screen containing the stream is actually active (cached screens skip watchdogs by design).
- **Manual reproduction**: `surveillance/tools/test_mpv_subprocess.py` can launch mpv with the same geometry/options to isolate binary-level issues before reintroducing the full carousel ([surveillance/tools/test_mpv_subprocess.py#L1-L105](surveillance/tools/test_mpv_subprocess.py#L1-L105)).
- **Diagnose rapid flapping**: repeated `detected exit code` logs indicate mpv is exiting cleanly but immediately. Compare timestamps with upstream RTSP probe failures or ffprobe timeouts earlier in the log to distinguish network vs. renderer issues.
- **Crash cleanup**: when mpv locks up without exiting, `stop_stream()` forces a kill. If zombie windows persist, confirm X11 tooling (`wmctrl`, `xdotool`) is present on Linux or that Windows DPI scaling is correctly reported so coordinates map 1:1 ([surveillance/core/Stream.py#L248-L314](surveillance/core/Stream.py#L248-L314)).

## Known Gaps & Follow-ups
- No exponential backoff—streams are restarted immediately upon failure, which can thrash unstable cameras. Consider adding per-stream cooldowns.
- Cached screens never run watchdogs; a long-lived failure may go unnoticed until rotation brings the stream on-screen.
- mpv stdout/stderr is not captured; troubleshooting relies on external log files or reproductions via `test_mpv_subprocess.py`.
- Windows window-management hooks (`wmctrl`, `xdotool`) are skipped; documenting PowerShell equivalents could improve parity.
