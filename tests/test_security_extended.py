import pytest
from cryptography.fernet import Fernet

from shopaware.security import SecretStore, build_runtime_url, clean_camera_url, masked_camera_url, redact


@pytest.mark.parametrize('url,clean', [
    ('rtsp://old:secret@10.0.0.1:554/live', 'rtsp://10.0.0.1:554/live'),
    ('rtsp://old:secret@[2001:db8::1]:554/live?channel=1', 'rtsp://[2001:db8::1]:554/live?channel=1'),
    ('rtsp://[::1]/live', 'rtsp://[::1]/live'),
])
def test_clean_ipv4_ipv6_credentials(url, clean):
    assert clean_camera_url(url) == clean
    assert '********' in masked_camera_url(url, 'u', True)


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'rtsp:///live', 'rtsp://[bad/live',
    'rtsp://host:abc/live', 'rtsp://host:65536/live', 'rtsp://host:0/live',
    'rtsp://host /live', 'rtsp://host/\nlive', 'rtsp://host/live#password'])
def test_malformed_url_rejected(url):
    with pytest.raises(ValueError):
        clean_camera_url(url)


def test_special_characters_and_encoding_roundtrip():
    from urllib.parse import unquote, urlsplit
    url = build_runtime_url('rtsp://[::1]:554/live', 'u @:/%', 'p@:/% +ü')
    parsed = urlsplit(url)
    assert unquote(parsed.username) == 'u @:/%'
    assert unquote(parsed.password) == 'p@:/% +ü'
    assert parsed.hostname == '::1'


def test_password_only_is_not_silently_dropped():
    assert build_runtime_url('rtsp://host/live', '', 'pass') == 'rtsp://:pass@host/live'
    assert masked_camera_url('rtsp://host/live', '', True) == 'rtsp://:********@host/live'


def test_at_rest_key_reuse_and_wrong_key_failure(tmp_path, monkeypatch):
    monkeypatch.delenv('SHOPAWARE_FERNET_KEY', raising=False)
    path = tmp_path / 'key'
    first = SecretStore(path)
    value = first.encrypt('camera secret')
    assert 'camera secret' not in value
    assert SecretStore(path).decrypt(value) == 'camera secret'
    monkeypatch.setenv('SHOPAWARE_FERNET_KEY', Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match='Unable to decrypt'):
        SecretStore(path).decrypt(value)


def test_environment_key_does_not_create_key_file(tmp_path, monkeypatch):
    monkeypatch.setenv('SHOPAWARE_FERNET_KEY', Fernet.generate_key().decode())
    path = tmp_path / 'absent'
    assert SecretStore(path).decrypt('') == ''
    assert not path.exists()
    monkeypatch.setenv('SHOPAWARE_FERNET_KEY', 'invalid')
    with pytest.raises(ValueError):
        SecretStore(path)


def test_redacts_unknown_url_credentials_and_encoded_known_secret():
    message = redact('failed rtsp://someone:p%40ss@[::1]/live and p%40ss', ['p@ss'])
    assert 'someone' not in message
    assert 'p%40ss' not in message
