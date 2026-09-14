"""Local administrators, Argon2id passwords and revocable server-side sessions."""
from __future__ import annotations

import getpass
import hashlib
import os
import secrets
import threading
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

from shopaware.db import Database, utc_now_iso

COOKIE = 'shopaware_session'
hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


class AuthService:
    def __init__(self, database: Database, lifetime: float = 43200):
        self.database = database
        self.lifetime = lifetime
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(32))
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def create_admin(self, username: str, password: str) -> None:
        if not 1 <= len(username.strip()) <= 128 or not 12 <= len(password) <= 1024:
            raise ValueError('Username required; password must contain 12â€“1024 characters')
        conn = self.database.connect()
        try:
            conn.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)',
                         (username.strip(), hasher.hash(password), 'admin', utc_now_iso()))
            conn.commit()
        finally:
            conn.close()

    def login(self, username: str, password: str, remote: str, now: float | None = None) -> tuple[str, dict] | None:
        now = time.time() if now is None else now
        with self._lock:
            self._attempts = {k: [t for t in v if now - t < 300] for k, v in self._attempts.items() if v and now - v[-1] < 300}
            attempts = self._attempts.setdefault(remote, [])
            if len(attempts) >= 10 or len(self._attempts) > 1024:
                raise PermissionError('Too many login attempts')
            attempts.append(now)
        conn = self.database.connect()
        try:
            user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            try:
                valid = hasher.verify(user['password_hash'] if user else self._dummy_hash, password)
            except (VerificationError, InvalidHashError):
                return None
            if not user or not valid or not user['enabled']:
                return None
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            expires = now + self.lifetime
            conn.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
            conn.execute('INSERT INTO sessions VALUES(?,?,?,?)',
                         (hashlib.sha256(token.encode()).hexdigest(), user['id'], csrf, expires))
            conn.commit()
            with self._lock:
                self._attempts.pop(remote, None)
            return token, dict(id=user['id'], username=user['username'], role=user['role'], csrf=csrf, expires_at=expires)
        finally:
            conn.close()

    def session(self, token: str | None, now: float | None = None) -> dict | None:
        if not token or len(token) > 128:
            return None
        now = time.time() if now is None else now
        conn = self.database.connect()
        try:
            row = conn.execute('SELECT u.id,u.username,u.role,s.csrf,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1',
                               (hashlib.sha256(token.encode()).hexdigest(), now)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def logout(self, token: str | None) -> None:
        conn = self.database.connect()
        try:
            conn.execute('DELETE FROM sessions WHERE token_hash=?',
                         (hashlib.sha256((token or '').encode()).hexdigest(),))
            conn.commit()
        finally:
            conn.close()

    def change_password(self, user_id: int, current: str, replacement: str) -> None:
        if not 12 <= len(replacement) <= 1024:
            raise ValueError('New password must contain 12–1024 characters')
        now = time.time()
        key = f'password:{user_id}'
        with self._lock:
            attempts = [t for t in self._attempts.get(key, []) if now - t < 300]
            if len(attempts) >= 10:
                raise PermissionError('Too many password attempts; try again later')
            self._attempts[key] = attempts + [now]
        conn = self.database.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            user = conn.execute('SELECT password_hash FROM users WHERE id=? AND enabled=1', (user_id,)).fetchone()
            try:
                valid = user and hasher.verify(user['password_hash'], current)
            except (VerificationError, InvalidHashError):
                valid = False
            if not valid:
                raise ValueError('Current password is incorrect')
            conn.execute('UPDATE users SET password_hash=? WHERE id=?', (hasher.hash(replacement), user_id))
            conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            conn.commit()
            with self._lock:
                self._attempts.pop(key, None)
        finally:
            conn.close()


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    username = input('Admin username: ').strip()
    password = getpass.getpass('New password (minimum 12 characters): ')
    if password != getpass.getpass('Repeat password: '):
        raise SystemExit('Passwords do not match')
    AuthService(Database(Path(os.getenv('SHOPAWARE_DB_PATH', 'shopaware.db')))).create_admin(username, password)
    print('Local administrator created.')


if __name__ == '__main__':
    main()
