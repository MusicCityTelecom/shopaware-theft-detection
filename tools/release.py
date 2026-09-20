"""Validate release identity and publish only the commit qualified by push CI.

Uses the standard library so the container job needs no application dependencies.
Existing tags are immutable, including annotated tags; release targetCommitish is
deliberately not used as proof of the commit a tag resolves to.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def validate(root: Path, event: str, ref: str, sha: str, head: str) -> str:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-beta\.[1-9]\d*", version
    ):
        raise ValueError("VERSION must be a beta release version")
    tag = f"v{version}"
    if event != "push" or ref != f"refs/heads/release/{tag}":
        raise ValueError("Only a push to the matching release branch may publish")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or head != sha:
        raise ValueError("Checkout does not match the qualified release SHA")
    if not (root / "docs" / "releases" / f"{tag}.md").is_file():
        raise ValueError("Release notes are missing")
    compose = (root / "deploy" / "server2" / "docker-compose.yml").read_text(encoding="utf-8")
    for image in ("backend", "dashboard"):
        if not re.search(rf"^\s*image: shopaware/{image}:{re.escape(version)}\s*$", compose, re.M):
            raise ValueError(f"Server2 {image} image version differs from VERSION")
    return tag


def remote_tag(tag: str) -> str | None:
    ref = f"refs/tags/{tag}"
    output = run("git", "ls-remote", "origin", ref, f"{ref}^{{}}")
    refs = dict(line.split()[::-1] for line in output.splitlines())
    return refs.get(f"{ref}^{{}}", refs.get(ref))


def publish(tag: str, sha: str) -> None:
    target = remote_tag(tag)
    if target is not None and target != sha:
        raise ValueError(f"Refusing to reuse {tag}: {target} != qualified SHA {sha}")
    if target is None:
        # Create the tag explicitly: gh --target alone can reuse a stale tag.
        # A concurrent conflicting tag creation fails the push, never overwrites.
        run("git", "push", "origin", f"{sha}:refs/tags/{tag}")
    if remote_tag(tag) != sha:
        raise ValueError("Remote tag does not resolve to the qualified release SHA")
    existing = subprocess.run(
        ["gh", "release", "view", tag], capture_output=True, text=True
    )
    if existing.returncode == 0:
        print(f"{tag} already exists at qualified SHA {sha}")
        return
    # API/auth failures also fail create; no existing release or tag is rewritten.
    run("gh", "release", "create", tag, "--verify-tag", "--target", sha,
        "--title", f"ShopAware {tag}", "--notes-file", f"docs/releases/{tag}.md",
        "--prerelease")
    if remote_tag(tag) != sha:
        raise ValueError("Release tag changed during publication")
    print(f"Published {tag} at qualified SHA {sha}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sha = os.environ.get("GITHUB_SHA", "")
    tag = validate(root, os.environ.get("GITHUB_EVENT_NAME", ""),
                   os.environ.get("GITHUB_REF", ""), sha, run("git", "rev-parse", "HEAD"))
    print(f"Validated release identity: {tag} at {sha}")
    if args.publish:
        publish(tag, sha)


if __name__ == "__main__":
    main()
