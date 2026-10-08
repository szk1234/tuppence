import re

import pytest

from check_release_version import main, package_versions, pep440


def tag_for(version: str) -> str:
    """The git tag for a PEP 440 version: 0.2.0.dev1 → v0.2.0-dev.1."""
    found = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(\.dev|a|b|rc)(\d+))?", version)
    assert found, version
    base, stage, number = found.groups()
    names = {".dev": "dev", "a": "alpha", "b": "beta", "rc": "rc"}
    return f"v{base}" if stage is None else f"v{base}-{names[stage]}.{number}"


@pytest.mark.parametrize(
    "tag,version",
    [
        ("v0.2.0-dev.1", "0.2.0.dev1"),
        ("v0.2.0-rc.2", "0.2.0rc2"),
        ("v1.0.0", "1.0.0"),
        ("v1.2.3-beta.4", "1.2.3b4"),
    ],
)
def test_tags_map_to_pep440(tag, version):
    assert pep440(tag) == version and tag_for(version) == tag


@pytest.mark.parametrize("tag", ["0.2.0", "v0.2", "v0.2.0-dev", "v0.2.0.dev1", "latest"])
def test_odd_tags_are_refused(tag):
    with pytest.raises(ValueError):
        pep440(tag)


def test_the_tag_must_match_the_package(capsys):
    project, module = package_versions()
    assert project == module
    assert main([tag_for(project)]) == 0
    assert main(["v9.9.9"]) == 1 and "but pyproject.toml says" in capsys.readouterr().err
