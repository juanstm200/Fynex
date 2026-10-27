"""Precarga lo que Python importa «perezosamente» (códecs, urllib, ssl...) y las configuraciones,
para que la app no dependa de archivos temporales que alguien pueda borrar mientras está abierta."""
import codecs
import importlib

from . import system

ENCODINGS = ("oem", "mbcs", "idna", "cp437", "cp850", "cp1252", "utf_16_le", "utf_8_sig", "latin_1", "ascii")
MODULES = ("urllib.request", "urllib.error", "http.client", "ssl", "socket", "json", "fnmatch",
           "xml.etree.ElementTree", "subprocess", "tkinter.messagebox", "winreg", "ctypes.wintypes")
CONFIGS = ("bloatware.json", "glpi.json", "winget_approved.json")


def warm_up() -> None:
    for enc in ENCODINGS:
        try:
            codecs.lookup(enc)
        except LookupError:
            pass                      # p. ej. «oem»/«mbcs» no existen fuera de Windows
    try:
        "girag".encode("idna")       # carga el códec que usa urllib al resolver nombres de host
    except Exception:
        pass
    for mod in MODULES:
        try:
            importlib.import_module(mod)
        except Exception:
            pass
    for name in CONFIGS:
        try:
            system.load_json_config(name)
        except Exception:
            pass
