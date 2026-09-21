"""Customer groups and server-side camera authorization."""
import json
import uuid
from contextlib import contextmanager
from shopaware.auth import hasher
from shopaware.db import utc_now_iso


class AccessControl:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def connection(self, write=False):
        conn = self.database.connect()
        try:
            if write:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
            if write:
                conn.commit()
        finally:
            conn.close()

    @staticmethod
    def scope(user):
        if user['role'] == 'admin':
            return '1', []
        return '''(EXISTS(SELECT 1 FROM user_camera_access a WHERE a.user_id=? AND a.camera_id=c.id)
            OR EXISTS(SELECT 1 FROM user_group_access a WHERE a.user_id=? AND a.group_id=c.group_id))''', [user['id'], user['id']]

    def cameras(self, user):
        scope, args = self.scope(user)
        with self.connection() as conn:
            return {r['id']: dict(r) for r in conn.execute(f'''SELECT c.id,c.group_id,c.access_epoch,g.name AS group_name
                FROM cameras c LEFT JOIN camera_groups g ON g.id=c.group_id WHERE {scope}''', args)}

    def can_incident(self, user, incident_id):
        if user['role'] == 'admin':
            return True
        scope, args = self.scope(user)
        with self.connection() as conn:
            return conn.execute(f'''SELECT 1 FROM incidents i JOIN cameras c ON c.id=i.camera_id
                WHERE i.id=? AND i.group_id IS c.group_id AND i.access_epoch=c.access_epoch AND {scope}''', [incident_id, *args]).fetchone() is not None

    def can_observation(self, user, observation_id):
        if user['role'] == 'admin':
            return True
        scope, args = self.scope(user)
        with self.connection() as conn:
            return conn.execute(f'''SELECT 1 FROM observations o JOIN cameras c ON c.id=o.camera_id
                WHERE o.id=? AND o.group_id IS c.group_id AND o.access_epoch=c.access_epoch AND {scope}''',
                [observation_id, *args]).fetchone() is not None

    def observations(self, user, limit, mode=None):
        scope, args = self.scope(user)
        if user['role'] != 'admin':
            scope += ' AND o.group_id IS c.group_id AND o.access_epoch=c.access_epoch'
        mode_clause = ' AND o.mode=?' if mode else ''
        if mode:
            args.append(mode)
        with self.connection() as conn:
            rows = conn.execute(f'''SELECT o.*,g.name AS group_name FROM observations o
                LEFT JOIN cameras c ON c.id=o.camera_id LEFT JOIN camera_groups g ON g.id=o.group_id
                WHERE {scope}{mode_clause} ORDER BY o.observed_at DESC LIMIT ?''', [*args, limit]).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['metadata'] = json.loads(item.pop('metadata_json') or '{}')
            item['snapshot_path'] = 'snapshot' if item['snapshot_path'] else None
            result.append(item)
        return result

    def history(self, user, limit):
        scope, args = self.scope(user)
        if user['role'] != 'admin':
            scope += ' AND i.group_id IS c.group_id AND i.access_epoch=c.access_epoch'
        with self.connection() as conn:
            rows = conn.execute(f'''SELECT i.*,g.name AS group_name FROM incidents i
                LEFT JOIN cameras c ON c.id=i.camera_id LEFT JOIN camera_groups g ON g.id=i.group_id
                WHERE {scope} ORDER BY i.created_at DESC LIMIT ?''', [*args, limit]).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['metadata'] = json.loads(item.pop('metadata_json') or '{}')
            if user['role'] != 'admin':
                for kind in ['snapshot', 'clip']:
                    item[kind + '_path'] = kind if item[kind + '_path'] else None
            result.append(item)
        return result

    def incident_counts(self, user):
        scope, args = self.scope(user)
        if user['role'] != 'admin':
            scope += ' AND i.group_id IS c.group_id AND i.access_epoch=c.access_epoch'
        with self.connection() as conn:
            return {r['review_status']: r['count'] for r in conn.execute(f'''SELECT i.review_status,COUNT(*) AS count
                FROM incidents i LEFT JOIN cameras c ON c.id=i.camera_id WHERE {scope} GROUP BY i.review_status''', args)}

    def groups(self, user):
        cameras = self.cameras(user)
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM camera_groups ORDER BY name COLLATE NOCASE').fetchall()
            grants = {r[0] for r in conn.execute('SELECT group_id FROM user_group_access WHERE user_id=?', (user['id'],))}
        visible = {c['group_id'] for c in cameras.values()} | grants
        return [dict(r, camera_count=sum(c['group_id'] == r['id'] for c in cameras.values()))
                for r in rows if user['role'] == 'admin' or r['id'] in visible]

    def save_group(self, name, group_id=None):
        name = name.strip()
        if not name or len(name) > 128:
            raise ValueError('Customer group name is required (maximum 128 characters)')
        with self.connection(True) as conn:
            if group_id:
                if conn.execute('UPDATE camera_groups SET name=? WHERE id=?', (name, group_id)).rowcount != 1:
                    raise LookupError('Group not found')
            else:
                group_id = str(uuid.uuid4())
                conn.execute('INSERT INTO camera_groups VALUES(?,?,?)', (group_id, name, utc_now_iso()))
        return {'id': group_id, 'name': name}

    def delete_group(self, group_id):
        with self.connection(True) as conn:
            if conn.execute('SELECT 1 FROM cameras WHERE group_id=?', (group_id,)).fetchone():
                raise ValueError('Move the cameras out of this group before deleting it')
            if conn.execute('DELETE FROM camera_groups WHERE id=?', (group_id,)).rowcount != 1:
                raise LookupError('Group not found')

    def move_camera(self, camera_id, group_id):
        with self.connection(True) as conn:
            camera = conn.execute('SELECT group_id FROM cameras WHERE id=?', (camera_id,)).fetchone()
            if not camera:
                raise LookupError('Camera not found')
            if group_id and not conn.execute('SELECT 1 FROM camera_groups WHERE id=?', (group_id,)).fetchone():
                raise LookupError('Group not found')
            if camera['group_id'] != group_id:
                # Moving a camera to another customer must not carry old direct grants.
                conn.execute('DELETE FROM user_camera_access WHERE camera_id=?', (camera_id,))
                conn.execute('UPDATE cameras SET group_id=?,access_epoch=access_epoch+1,updated_at=? WHERE id=?', (group_id, utc_now_iso(), camera_id))

    def users(self):
        with self.connection() as conn:
            result = []
            for row in conn.execute('SELECT id,username,role,enabled,created_at FROM users ORDER BY username COLLATE NOCASE'):
                item = dict(row, enabled=bool(row['enabled']))
                item['camera_ids'] = [r[0] for r in conn.execute('SELECT camera_id FROM user_camera_access WHERE user_id=?', (row['id'],))]
                item['group_ids'] = [r[0] for r in conn.execute('SELECT group_id FROM user_group_access WHERE user_id=?', (row['id'],))]
                result.append(item)
            return result

    def save_user(self, actor_id, payload, user_id=None):
        username = payload.username.strip()
        if not username:
            raise ValueError('Username is required')
        password = getattr(payload, 'password', None)
        if user_id is None and not password:
            raise ValueError('A password is required for a new user')
        with self.connection(True) as conn:
            if user_id is not None:
                old = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
                if not old:
                    raise LookupError('User not found')
                if actor_id == user_id and (not payload.enabled or payload.role != 'admin'):
                    raise ValueError('You cannot disable or demote your own administrator account')
                self._protect_last_admin(conn, old, not payload.enabled or payload.role != 'admin')
                conn.execute('UPDATE users SET username=?,role=?,enabled=? WHERE id=?',
                             (username, payload.role, int(payload.enabled), user_id))
            else:
                user_id = conn.execute('INSERT INTO users(username,password_hash,role,enabled,created_at) VALUES(?,?,?,?,?)',
                    (username, hasher.hash(password), payload.role, int(payload.enabled), utc_now_iso())).lastrowid
            for field, table, column, source in [('camera_ids', 'user_camera_access', 'camera_id', 'cameras'),
                                                 ('group_ids', 'user_group_access', 'group_id', 'camera_groups')]:
                values = set(getattr(payload, field))
                if any(not conn.execute(f'SELECT 1 FROM {source} WHERE id=?', (value,)).fetchone() for value in values):
                    raise ValueError('An assigned camera or group no longer exists')
                conn.execute(f'DELETE FROM {table} WHERE user_id=?', (user_id,))
                conn.executemany(f'INSERT INTO {table}(user_id,{column}) VALUES(?,?)', [(user_id, value) for value in values])
            conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        return {'id': user_id}

    @staticmethod
    def _protect_last_admin(conn, user, removing):
        if removing and user['role'] == 'admin' and user['enabled']:
            if conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0] <= 1:
                raise ValueError('At least one enabled administrator is required')

    def reset_password(self, actor_id, user_id, password):
        if actor_id == user_id:
            raise ValueError('Use My account to change your own password')
        with self.connection(True) as conn:
            if conn.execute('UPDATE users SET password_hash=? WHERE id=?', (hasher.hash(password), user_id)).rowcount != 1:
                raise LookupError('User not found')
            conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))

    def delete_user(self, actor_id, user_id):
        if actor_id == user_id:
            raise ValueError('You cannot delete your own account')
        with self.connection(True) as conn:
            user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not user:
                raise LookupError('User not found')
            self._protect_last_admin(conn, user, True)
            conn.execute('DELETE FROM users WHERE id=?', (user_id,))
