from types import SimpleNamespace

import pytest

from tools import release


SHA = "a" * 40
TAG = "v0.1.0-beta.7"
REF = f"refs/heads/release/{TAG}"


@pytest.fixture
def release_tree(tmp_path):
    (tmp_path / "VERSION").write_text("0.1.0-beta.7\n")
    notes = tmp_path / "docs" / "releases"
    notes.mkdir(parents=True)
    (notes / f"{TAG}.md").write_text("Release notes")
    compose = tmp_path / "deploy" / "server2"
    compose.mkdir(parents=True)
    (compose / "docker-compose.yml").write_text(
        "image: shopaware/backend:0.1.0-beta.7\nimage: shopaware/dashboard:0.1.0-beta.7\n"
    )
    return tmp_path


def test_valid_release_identity(release_tree):
    assert release.validate(release_tree, "push", REF, SHA, SHA) == TAG


@pytest.mark.parametrize("event,ref,sha,head", [
    ("pull_request", REF, SHA, SHA),
    ("push", "refs/heads/release/v0.1.0-beta.6", SHA, SHA),
    ("push", "refs/heads/main", SHA, SHA),
    ("push", REF, SHA, "b" * 40),
    ("push", REF, "", ""),
])
def test_rejects_unqualified_source(release_tree, event, ref, sha, head):
    with pytest.raises(ValueError):
        release.validate(release_tree, event, ref, sha, head)


@pytest.mark.parametrize("version", ["0.1.0", "0.1.0-beta.0", "../bad", "0.1.0-beta.7\ninjected"])
def test_rejects_bad_version(release_tree, version):
    (release_tree / "VERSION").write_text(version)
    with pytest.raises(ValueError, match="VERSION"):
        release.validate(release_tree, "push", REF, SHA, SHA)


def test_rejects_missing_notes(release_tree):
    (release_tree / "docs" / "releases" / f"{TAG}.md").unlink()
    with pytest.raises(ValueError, match="notes"):
        release.validate(release_tree, "push", REF, SHA, SHA)


@pytest.mark.parametrize("image", ["backend", "dashboard"])
def test_rejects_stale_container_version(release_tree, image):
    path = release_tree / "deploy" / "server2" / "docker-compose.yml"
    path.write_text(path.read_text().replace(f"{image}:0.1.0-beta.7", f"{image}:0.1.0-beta.6"))
    with pytest.raises(ValueError, match=image):
        release.validate(release_tree, "push", REF, SHA, SHA)


@pytest.mark.parametrize("annotated", [False, True])
def test_resolves_actual_remote_tag_commit(monkeypatch, annotated):
    output = f"{SHA}\trefs/tags/{TAG}"
    if annotated:
        output = f"{'b' * 40}\trefs/tags/{TAG}\n{SHA}\trefs/tags/{TAG}^{{}}"
    monkeypatch.setattr(release, "run", lambda *args: output)
    assert release.remote_tag(TAG) == SHA


def test_stale_tag_never_publishes_or_moves(monkeypatch):
    monkeypatch.setattr(release, "remote_tag", lambda tag: "b" * 40)
    monkeypatch.setattr(release, "run", lambda *args: pytest.fail("Must not mutate stale tag"))
    with pytest.raises(ValueError, match="Refusing"):
        release.publish(TAG, SHA)


def test_creates_and_verifies_tag_before_release(monkeypatch):
    targets = iter([None, SHA, SHA])
    monkeypatch.setattr(release, "remote_tag", lambda tag: next(targets))
    calls = []
    monkeypatch.setattr(release, "run", lambda *args: calls.append(args))
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    release.publish(TAG, SHA)
    assert calls[0] == ("git", "push", "origin", f"{SHA}:refs/tags/{TAG}")
    assert calls[1][:4] == ("gh", "release", "create", TAG)
    assert "--verify-tag" in calls[1]


def test_matching_release_retry_does_not_rewrite(monkeypatch):
    monkeypatch.setattr(release, "remote_tag", lambda tag: SHA)
    monkeypatch.setattr(release, "run", lambda *args: pytest.fail("Must not rewrite release"))
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    release.publish(TAG, SHA)


def test_tag_race_fails_before_release(monkeypatch):
    targets = iter([None, "b" * 40])
    monkeypatch.setattr(release, "remote_tag", lambda tag: next(targets))
    calls = []
    monkeypatch.setattr(release, "run", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="Remote tag"):
        release.publish(TAG, SHA)
    assert len(calls) == 1
