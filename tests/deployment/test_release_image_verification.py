import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_release_image", ROOT / "deploy" / "verify_release_image.py"
)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def test_subpath_frontend_assets_are_accepted():
    html = (
        '<link rel="icon" href="/automation/assets/favicon.svg">'
        '<script src="/automation/assets/index.js"></script>'
        '<link rel="stylesheet" href="/automation/assets/index.css">'
    )
    assert len(VERIFY.verify_frontend_index(html, "/automation")) == 3


def test_root_built_frontend_is_rejected_for_subpath():
    with pytest.raises(ValueError, match="没有使用指定子路径"):
        VERIFY.verify_frontend_index('<script src="/assets/index.js"></script>', "/automation")


@pytest.mark.parametrize("value", ["automation", "/a/../b", "/a//b", "/a?b", "/a b"])
def test_unsafe_base_path_is_rejected(value):
    with pytest.raises(ValueError):
        VERIFY.normalized_base_path(value)


def test_release_metadata_must_match_platform_version_and_commit():
    commit = "a" * 40
    metadata = {
        "Id": "sha256:synthetic",
        "Os": "linux",
        "Architecture": "amd64",
        "Size": 123,
        "Config": {"Labels": {
            "org.opencontainers.image.version": "0.6.0-rc.3",
            "org.opencontainers.image.revision": commit,
        }},
    }
    assert VERIFY.verify_metadata(metadata, "0.6.0-rc.3", commit)["size_bytes"] == 123
    metadata["Architecture"] = "arm64"
    with pytest.raises(ValueError, match="linux/amd64"):
        VERIFY.verify_metadata(metadata, "0.6.0-rc.3", commit)
