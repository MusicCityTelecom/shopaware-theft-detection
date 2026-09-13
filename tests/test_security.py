from backend import build_runtime_rtsp_url, masked_rtsp_url, redact_exception_message


def test_runtime_rtsp_url_injects_encoded_credentials():
    url = build_runtime_rtsp_url(
        "rtsp://10.0.0.25:554/Streaming/Channels/101",
        "camera user",
        "p@ss:word",
    )
    assert url.startswith("rtsp://camera%20user:p%40ss%3Aword@10.0.0.25:554/")


def test_runtime_rtsp_url_strips_embedded_credentials():
    url = build_runtime_rtsp_url(
        "rtsp://old:bad@10.0.0.25:554/live",
        "newuser",
        "newpass",
    )
    assert "old" not in url
    assert "bad" not in url
    assert "newuser:newpass@" in url


def test_masked_url_never_contains_password():
    safe = masked_rtsp_url(
        "rtsp://10.0.0.25:554/live",
        "operator",
        True,
    )
    assert safe == "rtsp://operator:********@10.0.0.25:554/live"


def test_exception_redaction():
    secret = "super-secret-camera-password"
    message = redact_exception_message(f"connection failed using {secret}", [secret])
    assert secret not in message
    assert "********" in message
