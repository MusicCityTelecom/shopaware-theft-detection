import json
from shopaware.db import Database
from shopaware.security import SecretStore
from shopaware.settings import SettingsInput, SettingsStore, ENV_FIELDS


def test_smtp_replacement_encrypted_and_never_returned(tmp_path, monkeypatch):
    import os
    for key in ENV_FIELDS.values():
        if key in os.environ:
            monkeypatch.setenv(key, os.environ[key])
        else:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv('SMTP_PASSWORD', raising=False)
    db = Database(tmp_path / 'db')
    store = SettingsStore(db, SecretStore(tmp_path / 'key'))
    result = store.save(SettingsInput(smtp_password='synthetic-smtp-secret'))
    assert 'synthetic-smtp-secret' not in json.dumps(result)
    assert result['has_smtp_password']
    conn = db.connect()
    try:
        assert 'synthetic-smtp-secret' not in conn.execute('SELECT value_json FROM settings').fetchone()[0]
    finally:
        conn.close()
    store.save(SettingsInput(smtp_host='smtp.example.test'))
    assert store.public()['has_smtp_password']
    monkeypatch.setenv('SMTP_PASSWORD', '')
    store.load_environment()
    import os
    assert os.environ['SMTP_PASSWORD'] == 'synthetic-smtp-secret'
    store.save(SettingsInput(smtp_password=''))
    store.load_environment()
    assert not store.public()['has_smtp_password']
