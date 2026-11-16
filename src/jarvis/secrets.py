from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from .config import atomic_json, data_dir


if os.name == "nt":
    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _protect(value: str) -> str:
    if os.name != "nt":
        raise RuntimeError("Sichere Schlüsselspeicherung benötigt unter diesem System keyring.")
    data = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _Blob()
    if not _crypt32.CryptProtectData(ctypes.byref(source), "JARVIS", None, None, None, 0,
                                    ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return base64.b64encode(ctypes.string_at(target.pbData, target.cbData)).decode("ascii")
    finally:
        _kernel32.LocalFree(target.pbData)


def _unprotect(value: str) -> str:
    raw = base64.b64decode(value, validate=True)
    buffer = ctypes.create_string_buffer(raw)
    source = _Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _Blob()
    if not _crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0,
                                      ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
    finally:
        _kernel32.LocalFree(target.pbData)


class SecretStore:
    """Windows DPAPI per-user encryption; keyring on other platforms."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "secrets.json"

    def _read(self) -> dict[str, str]:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def get(self, key: str) -> str | None:
        if os.name != "nt":
            import keyring
            return keyring.get_password("jarvis-assistant", key)
        encoded = self._read().get(key)
        return _unprotect(encoded) if encoded else None

    def set(self, key: str, value: str) -> None:
        if not key or not value:
            raise ValueError("Schlüsselname und Wert dürfen nicht leer sein.")
        if os.name != "nt":
            import keyring
            keyring.set_password("jarvis-assistant", key, value)
            return
        values = self._read()
        values[key] = _protect(value)
        atomic_json(self.path, values)

    def delete(self, key: str) -> None:
        if os.name != "nt":
            import keyring
            try:
                keyring.delete_password("jarvis-assistant", key)
            except keyring.errors.PasswordDeleteError:
                pass
            return
        values = self._read()
        if key in values:
            del values[key]
            atomic_json(self.path, values)
