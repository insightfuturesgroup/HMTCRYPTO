"""Public-repository identifier checks use fake values and hash-only fixtures."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from tools import migrate_mumbai
from tools.migrate_mumbai import build_environment

pytestmark = pytest.mark.safety
ROOT = Path(__file__).parents[2]


def guard():
    spec = importlib.util.spec_from_file_location(
        "public_identifier_guard", ROOT / "tools/check_public_identifiers.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "kind,value",
    [
        ("project-ref", b"abcdefghijklmnopqrst"),
        ("instance-id", b"i-" + b"a" * 17),
        ("ipv4", b"192.0.2.17"),
    ],
)
def test_hash_guard_finds_embedded_identifiers_without_returning_values(kind, value):
    module = guard()
    record = {"kind": kind, "salt": "ab" * 32, "sha256": module.digest(value, "ab" * 32)}
    content = b"first line\nprefix" + value + b"suffix\n"
    assert module.scan_bytes(content, [record]) == [(kind, 2)]
    assert value.decode() not in json.dumps(record)
    assert value.decode() not in repr(module.scan_bytes(content, [record]))
    assert module.scan_bytes(b"unrelated fixture", [record]) == []


def test_hashes_are_salted_and_sensitive_to_one_byte_change():
    module = guard()
    assert module.digest(b"offline", "ab" * 32) != module.digest(b"offline", "cd" * 32)
    assert module.digest(b"offline", "ab" * 32) != module.digest(b"offlinf", "ab" * 32)


def test_manifest_contains_all_five_hash_only_records():
    module = guard()
    records = module.load_records(ROOT / "tools/public_identifier_fingerprints.json")
    assert Counter(record["kind"] for record in records) == {
        "project-ref": 2,
        "ipv4": 2,
        "instance-id": 1,
    }
    assert all(set(record) == {"kind", "salt", "sha256"} for record in records)


def test_guard_rejects_tracked_operator_configuration_without_reading_it(tmp_path):
    module = guard()
    # The path need not exist: protection is checked before reading file contents.
    with pytest.raises(ValueError, match="Tracked operator configuration"):
        module.scan_paths(tmp_path, ["ops/local.env"], [])


def test_guard_does_not_follow_a_symlink_to_operator_configuration(tmp_path):
    module = guard()
    (tmp_path / "reference.txt").symlink_to("ops/local.env")
    assert module.scan_paths(tmp_path, ["reference.txt"], []) == []


def test_incomplete_manifest_fails_closed(tmp_path):
    module = guard()
    path = tmp_path / "fingerprints.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "fingerprints": [{"kind": "ipv4", "salt": "ab" * 32, "sha256": "cd" * 32}],
            }
        )
    )
    with pytest.raises(ValueError, match="Incomplete fingerprint manifest"):
        module.load_records(path)


def test_ci_runs_identifier_guard_before_image_build():
    source = (ROOT / ".github/workflows/verify.yml").read_text()
    assert source.index("python tools/check_public_identifiers.py") < source.index("docker build")


def test_migration_environment_requires_explicit_fake_project(tmp_path):
    values = build_environment(
        tmp_path / "fake-ca.crt",
        "aws-0-ap-south-1.pooler.supabase.com",
        "owner@/%fixture",
        "runtime@/%fixture",
        project_ref="abcdefghijklmnopqrst",
    )
    owner = make_url(values["DATABASE_MIGRATION_URL"])
    runtime = make_url(values["DATABASE_URL"])
    assert owner.host == "db.abcdefghijklmnopqrst.supabase.co"
    assert runtime.username == "hmt_crypto_app.abcdefghijklmnopqrst"
    assert owner.port == runtime.port == 5432
    assert not owner.query and not runtime.query
    assert owner.password == "owner@/%fixture"


def test_project_reference_uses_hidden_prompt_without_a_default(monkeypatch):
    monkeypatch.delenv("HMT_SUPABASE_PROJECT_REF", raising=False)
    prompts = []

    def hidden(prompt):
        prompts.append(prompt)
        return "abcdefghijklmnopqrst"

    monkeypatch.setattr(migrate_mumbai.getpass, "getpass", hidden)
    assert migrate_mumbai.read_project_reference() == "abcdefghijklmnopqrst"
    assert prompts == ["Supabase project reference (hidden): "]


def test_project_reference_can_use_an_existing_private_environment(monkeypatch):
    monkeypatch.setenv("HMT_SUPABASE_PROJECT_REF", "abcdefghijklmnopqrst")
    monkeypatch.setattr(
        migrate_mumbai.getpass, "getpass", lambda _: pytest.fail("Unexpected prompt")
    )
    assert migrate_mumbai.read_project_reference() == "abcdefghijklmnopqrst"


@pytest.mark.parametrize("project", ["", "fake.invalid", "a" * 19, "a" * 21, "a/" * 10])
def test_invalid_project_is_rejected_without_echoing_input(tmp_path, project):
    with pytest.raises(ValueError, match="^Invalid project reference$"):
        build_environment(
            tmp_path / "fake-ca.crt",
            "aws-0-ap-south-1.pooler.supabase.com",
            "owner-fixture",
            "runtime-fixture",
            project_ref=project,
        )
