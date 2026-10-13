"""Girag · Mantenimiento preventivo para Windows 10/11."""
import sys

from girag_maint.core import system


def main():
    system.enable_dpi_awareness()
    if system.IS_WINDOWS and not system.is_admin() and "--no-elevate" not in sys.argv:
        if system.relaunch_as_admin():
            return
    from girag_maint.ui.app import App
    App().mainloop()


if __name__ == "__main__":
    main()
