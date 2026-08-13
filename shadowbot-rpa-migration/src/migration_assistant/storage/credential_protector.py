"""Windows DPAPI 凭据加解密 (ctypes)."""
import ctypes
import ctypes.wintypes as wt

from migration_assistant.errors import CredentialUnavailable


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_LocalFree = ctypes.windll.kernel32.LocalFree
_CryptProtectData = ctypes.windll.crypt32.CryptProtectData
_CryptUnprotectData = ctypes.windll.crypt32.CryptUnprotectData


class WindowsDPAPI:
    def __init__(self, entropy: bytes):
        self.entropy = entropy

    def protect(self, plaintext: str) -> bytes:
        return self._run(plaintext.encode("utf-8"), protect=True)

    def unprotect(self, blob: bytes) -> str:
        return self._run(blob, protect=False).decode("utf-8")

    def _run(self, data: bytes, *, protect: bool) -> bytes:
        in_blob = self._to_blob(data)
        out_blob = _DATA_BLOB()
        entropy_blob = self._to_blob(self.entropy)
        try:
            func = _CryptProtectData if protect else _CryptUnprotectData
            ok = func(ctypes.byref(in_blob), None,
                      ctypes.byref(entropy_blob), None, None,
                      _CRYPTPROTECT_UI_FORBIDDEN,
                      ctypes.byref(out_blob))
            if not ok:
                raise CredentialUnavailable()
            raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return bytes(raw)
        finally:
            if out_blob.pbData:
                _LocalFree(out_blob.pbData)

    @staticmethod
    def _to_blob(data: bytes) -> _DATA_BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
