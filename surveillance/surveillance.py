#!/usr/bin/python3
import logging
import signal
import sys
import time

if sys.platform == "win32":
    import ctypes
    import win32api
    MDT_EFFECTIVE_DPI = 0
else:
    import Xlib.display

from core.util.config import cfg
from core.util.setuplogging import setup_logging
from core.ScreenManager import ScreenManager


def _init_windows_dpi_awareness():
    """Ensure Windows places child processes using physical pixels."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except AttributeError:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    except OSError:
        # Fall back to older API if shcore call fails (pre-Windows 8.1).
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _get_monitor_dpi_scale(handle, hdc=None):
    if sys.platform != "win32":
        return 1.0

    log = logging.getLogger('l_default')
    try:
        shcore = getattr(ctypes.windll, "shcore", None)

        # Primary path: GetDpiForMonitor gives per-monitor DPI on Windows 8.1+
        if shcore is not None:
            dpi_x = ctypes.c_uint()
            dpi_y = ctypes.c_uint()
            result = shcore.GetDpiForMonitor(
                ctypes.c_void_p(int(handle)),
                MDT_EFFECTIVE_DPI,
                ctypes.byref(dpi_x),
                ctypes.byref(dpi_y),
            )
            if result == 0 and dpi_x.value:
                return dpi_x.value / 96.0
            log.warning("MAIN: GetDpiForMonitor failed for handle %s (code=%s)", handle, result)

            # Older Windows builds expose GetScaleFactorForMonitor instead.
            if hasattr(shcore, "GetScaleFactorForMonitor"):
                scale_factor = ctypes.c_int()
                legacy_result = shcore.GetScaleFactorForMonitor(
                    ctypes.c_void_p(int(handle)),
                    ctypes.byref(scale_factor),
                )
                if legacy_result == 0 and scale_factor.value:
                    return float(scale_factor.value) / 100.0
                log.warning(
                    "MAIN: GetScaleFactorForMonitor failed for handle %s (code=%s)",
                    handle,
                    legacy_result,
                )

        # Fall back to system DPI so users at least get consistent scaling.
        user32 = getattr(ctypes.windll, "user32", None)
        if user32 is not None and hasattr(user32, "GetDpiForSystem"):
            dpi = user32.GetDpiForSystem()
            if dpi:
                log.warning("MAIN: Falling back to GetDpiForSystem DPI=%s", dpi)
                return dpi / 96.0

        # Legacy fallback for environments missing newer APIs.
        gdi32 = getattr(ctypes.windll, "gdi32", None)
        if user32 is not None and gdi32 is not None:
            desktop_hdc = user32.GetDC(0)
            if desktop_hdc:
                LOGPIXELSX = 88
                dpi = gdi32.GetDeviceCaps(desktop_hdc, LOGPIXELSX)
                user32.ReleaseDC(0, desktop_hdc)
                if dpi:
                    log.warning("MAIN: Falling back to GetDeviceCaps DPI=%s", dpi)
                    return dpi / 96.0

    except Exception as exc:
        log.exception("MAIN: Unexpected DPI lookup failure: %s", exc)

    if hdc is not None:
        try:
            import win32con
            import win32gui

            dpi = win32gui.GetDeviceCaps(hdc, win32con.LOGPIXELSX)
            if dpi:
                log.warning(
                    "MAIN: Falling back to monitor HDC GetDeviceCaps DPI=%s for handle %s",
                    dpi,
                    handle,
                )
                return dpi / 96.0
        except Exception as exc:
            log.warning(
                "MAIN: monitor HDC GetDeviceCaps fallback failed for handle %s: %s",
                handle,
                exc,
            )

    log.warning("MAIN: Unable to determine DPI for monitor handle %s, defaulting to scale 1.0", handle)
    return 1.0

def _monitor_allowed(monitor_id):
    allowlist = cfg.get("advanced", {}).get("monitor_allowlist")
    if not allowlist:
        return True
    return str(monitor_id) in {str(mid) for mid in allowlist}

def get_monitors():
    monitor_list = []

    if sys.platform == "win32":
        for idx, (handle, _hdc, rect) in enumerate(win32api.EnumDisplayMonitors()):
            left, top, right, bottom = rect
            try:
                handle_str = str(int(handle))
            except (TypeError, ValueError):
                handle_str = str(handle)
            dpi_scale = _get_monitor_dpi_scale(handle, _hdc)
            monitor_info = {
                "xdisplay_id": "",  # DISPLAY not used on Windows
                "monitor_id": handle_str,
                "monitor_repr": str(handle),
                "monitor_number": idx,
                "resolution": {
                    "width": str(right - left),
                    "height": str(bottom - top)
                },
                "x_offset": str(left),
                "y_offset": str(top),
                "dpi_scale": dpi_scale,
            }
            if not _monitor_allowed(handle_str):
                logger.info(
                    "MAIN: get_monitors: skipping monitor %(monitor_repr)s due to monitor_allowlist",
                    {"monitor_repr": monitor_info.get("monitor_repr", monitor_info["monitor_id"])}
                )
                continue
            monitor_info["monitor_number"] = len(monitor_list)
            monitor_list.append(monitor_info)
    else:
        display = Xlib.display.Display()
        root = display.screen().root

        # Iterate over the monitors and create dictionaries
        for m in root.xrandr_get_monitors().monitors:
            connector = display.get_atom_name(m.name)
            monitor_dict = {
                "xdisplay_id": ":0.0",
                "monitor_id": connector,  # or use m.name if you want the name directly
                "resolution": {
                    "width": str(m.width_in_pixels),  # Convert to string as per your structure
                    "height": str(m.height_in_pixels)  # Convert to string as per your structure
                },
                "x_offset": str(m.x),  # Convert to string
                "y_offset": str(m.y)  # Convert to string
            }
            if not _monitor_allowed(connector):
                logger.info(
                    "MAIN: get_monitors: skipping monitor %(monitor_id)s due to monitor_allowlist",
                    {"monitor_id": connector},
                )
                continue
            monitor_dict["monitor_number"] = len(monitor_list)
            monitor_list.append(monitor_dict)

    if not monitor_list:
        logger.warning("MAIN: get_monitors: no monitors detected")
    else:
        for monitor in monitor_list:
            logger.info(
                "MAIN: get_monitors: available monitor %(monitor_number)s %(monitor_id)s "
                "%(width)sx%(height)s offset(%(x)s,%(y)s)",
                {
                    "monitor_number": monitor.get("monitor_number"),
                    "monitor_id": monitor.get("monitor_id"),
                    "width": monitor.get("resolution", {}).get("width"),
                    "height": monitor.get("resolution", {}).get("height"),
                    "x": monitor.get("x_offset"),
                    "y": monitor.get("y_offset"),
                },
            )
    logger.debug(f"MAIN: get_monitors: detected {monitor_list}")
    return monitor_list

def handle_input(background_drawinstance):
    event = background_drawinstance.check_input()
    for screenmanager in screenmanagers:
        if event == "next_event":
            logger.debug(f"MAIN: force next screen input event detected, start rotate_next event")
            screenmanager.rotate_next()
            # This can do no harm, but is not really needed in this case, since the screen was already updated in cache and we merely swap it on screen as is.
            # We do not check update_connectable_cameras this time as this is too slow for the user to wait for and we live with the fact if there is one unavailable or one became available since cache time,
            # it is not updated until next regular update of the screen
        if event == "end_event":
            logger.debug(f"MAIN: quit input event detected")
            for screenmanager in screenmanagers:
                logger.debug(f"MAIN: instruct {screenmanager.name} to be destroyed")
                screenmanager.destroy()
            sys.exit(0)
        if event == "resume_rotation":
            logger.debug(f"MAIN: resume_rotation event detected")
            screenmanager.disable_autorotation = False
        if event == "pause_rotation":
            logger.debug(f"MAIN: pause_rotation event detected")
            screenmanager.disable_autorotation = True
        if event in range(0, 11):
            logger.debug(f"MAIN: force screen:{event} request detected")
            screenmanager.force_show_screen(event)


def sigterm_handler(_signo, _stack_frame):
    for screenmanager in screenmanagers:
        logger.debug(f"MAIN: instruct {screenmanager.name} to be destroyed")
        screenmanager.destroy()
    sys.exit(0)


if __name__ == '__main__':


    signal.signal(signal.SIGTERM, sigterm_handler)

    #Setup logger
    logger = setup_logging()
    _init_windows_dpi_awareness()

    fullversion_for_installer = "1.3"

    version = fullversion_for_installer
    logger.info("Starting opensurv " + version)

    #Read in config
    interval_check_status=cfg['advanced']['interval_check_status'] if 'interval_check_status' in cfg["advanced"] else 19 #Override of interval_check_status if set
    enable_caching_next_screen=cfg['advanced']['enable_caching_next_screen'] if 'enable_caching_next_screen' in cfg["advanced"] else True #Override of enable_caching_next_screen if set

    #Detect displays attached and their config
    monitors=get_monitors()

    if not monitors:
        logger.error(
            "MAIN: no monitors available after applying monitor_allowlist=%s. Exiting.",
            cfg.get("advanced", {}).get("monitor_allowlist"),
        )
        sys.exit(1)

    screenmanagers=[]
    count=0
    for monitor in monitors:
        disable_pygame = True
        # Only one screenmanager may be the master of the pygame in the case we have multiple instances. choose the first screen detected
        if count == 0:
            disable_pygame = False
        screen_manager=ScreenManager(f'screen_manager_{count}', monitor, enable_caching_next_screen, disable_pygame)
        screenmanagers.append(screen_manager)
        count= count + 1


    #Bootstrap all screenmanagers
    for screenmanager in screenmanagers:
        logger.debug(f"MAIN {screenmanager.name}: START bootstrap")
        screenmanager.bootstrap()
        logger.debug(f"MAIN {screenmanager.name}: END bootstrap")
    loop_counter=0
    while True:
        try:
            loop_counter += 1

            #Handle keypresses
            #Only the first screenmanager is the controller of pygame
            handle_input(screenmanagers[0].get_background_drawinstance())

            #Check if we need to rotate:
            for screenmanager in screenmanagers:
                if not screenmanager.get_disable_autorotation():
                    if screenmanager.get_active_screen_run_time() >= screenmanager.get_active_screen_duration():
                        screenmanager.rotate_next()
                        #In case the screen in cache had disconnected or reconnectable streams, check and update it once it becomes active
                        logger.debug(f"MAIN {screenmanager.name}: after rotate_next start update_active_screen")
                        screenmanager.update_active_screen()

                #Only update the screen/check connectable cameras and repair every interval_check_status seconds
                if loop_counter % int(interval_check_status) == 0:
                    # Only try to redraw the screen when disable_probing_for_all_streams option is false, but keep the loop
                    logger.debug(f"MAIN {screenmanager.name}: regular start update_active_screen (every " + str(interval_check_status) + " seconds since start of opensurv)")
                    screenmanager.update_active_screen()
                    # Call the watchdogs to check and try to repair for crashed instances
                    screenmanager.run_watchdogs_active_screen()

                #Reset focus pygame for keyhandling
                screenmanager.focus_background_pygame()

            time.sleep(1)
        except Exception as e:
            logger.error(f"MAIN: crash { repr(e)}")
            exit(99)