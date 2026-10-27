"""Fynex · Mantenimiento preventivo para Windows 10/11."""
import sys

from fynex.core import system


_crash_file = None          # se mantiene abierto mientras viva el proceso (faulthandler lo necesita)


def _enable_crash_log():
    """Si el proceso muere por un fallo nativo (el .exe no tiene consola), deja el rastro en logs\\fynex_crash.log."""
    global _crash_file
    try:
        import faulthandler
        _crash_file = open(system.logs_dir() / "fynex_crash.log", "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except Exception:
        pass


def main():
    system.enable_dpi_awareness()
    system.set_app_id("Fynex.Maintenance")
    from fynex.core.runtime_guard import warm_up
    warm_up()
    if system.IS_WINDOWS and not system.is_admin() and "--no-elevate" not in sys.argv:
        if system.relaunch_as_admin():
            return
    _enable_crash_log()
    from fynex.ui.app import App
    App().mainloop()


if __name__ == "__main__":
    main()
