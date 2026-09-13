from shopaware.security import build_runtime_url, masked_camera_url, redact


def test_runtime_url_injects_encoded_credentials():
    url = build_runtime_url(
        "rtsp://10.0.0.25:554/Streaming/Channels/101",
        "camera user",
        "sample-value-1",
    )
    assert url.startswith("rtsp://camera%20user:sample-value-1@10.0.0.25:554/")


def test_runtime_url_strips_embedded_credentials():
    url = build_runtime_url(
        "rtsp://old:legacy@10.0.0.25:554/live",
        "newuser",
        "newvalue",
    )
    assert "old" not in url
    assert "legacy" not in url
    assert "newuser:newvalue@" in url


def test_masked_url_never_contains_secret_value():
    safe = masked_camera_url(
        "rtsp://10.0.0.25:554/live",
        "operator",
        True,
    )
    assert safe == "rtsp://operator:********@10.0.0.25:554/live"


def test_exception_redaction():
    secret = "sample-sensitive-value"
    message = redact(f"connection failed using {secret}", [secret])
    assert secret not in message
    assert "********" in message
