from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken


class SecretStore:
    def __init__(self, key_file: Path | None = None, env_var: str = "SHOPAWARE_FERNET_KEY") -> None:
        self.key_file = key_file or Path(os.getenv("SHOPAWARE_KEY_FILE", ".shopaware.key"))
        self.env_var = env_var
        self.fernet = self._load_or_create()

    def _load_or_create(self) -> Fernet:
        env_key = os.getenv(self.env_var, "").strip()
        if env_key:
            return Fernet(env_key.encode("utf-8"))
        if self.key_file.exists():
            return Fernet(self.key_file.read_bytes().strip())

        key = Fernet.generate_key()
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.key_file.open("xb") as stream:
                stream.write(key)
        except FileExistsError:
            return Fernet(self.key_file.read_bytes().strip())
        try:
            os.chmod(self.key_file, 0o600)
        except OSError:
            pass
        return Fernet(key)

    def encrypt(self, value: str | None) -> str:
        if not value:
            return ""
        return self.fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str | None) -> str:
        if not value:
            return ""
        try:
            return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise RuntimeError("Unable to decrypt stored credential") from exc


def clean_camera_url(url: str) -> str:
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise ValueError("Camera URL must not contain whitespace or control characters")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"rtsp", "rtsps", "http", "https"}:
        raise ValueError("Camera URL uses an unsupported scheme")
    if parts.fragment:
        raise ValueError("Camera URL must not contain a fragment")
    hostname = parts.hostname or ""
    if not hostname:
        raise ValueError("Camera URL must include a hostname or IP address")
    host = hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port is not None:
        if parts.port == 0:
            raise ValueError("Camera URL port must be between 1 and 65535")
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def build_runtime_url(url: str, username: str = "", password: str = "") -> str:
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"rtsp", "rtsps", "http", "https"}:
        raise ValueError("Camera URL must use rtsp://, rtsps://, http://, or https://")
    clean = urlsplit(clean_camera_url(url))
    host = clean.netloc
    auth = ""
    if username or password:
        auth = quote(username, safe="")
        if password:
            auth += f":{quote(password, safe='')}"
        auth += "@"
    return urlunsplit((clean.scheme, f"{auth}{host}", clean.path, clean.query, clean.fragment))


def masked_camera_url(url: str, username: str = "", has_password: bool = False) -> str:
    try:
        clean = urlsplit(clean_camera_url(url))
    except Exception:
        return "<invalid-camera-url>"
    auth = ""
    if username or has_password:
        auth = quote(username, safe="")
        if has_password:
            auth += ":********"
        auth += "@"
    return urlunsplit((clean.scheme, f"{auth}{clean.netloc}", clean.path, clean.query, clean.fragment))


def redact(message: str, secrets: list[str] | None = None) -> str:
    result = str(message)
    result = re.sub(r"(?i)((?:rtsp|rtsps|https?)://)[^\s/]+@", r"\1********@", result)
    for secret in secrets or []:
        if secret:
            for representation in {secret, quote(secret, safe=""), unquote(secret)}:
                if representation:
                    result = result.replace(representation, "********")
    return result
