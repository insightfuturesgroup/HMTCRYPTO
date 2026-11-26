"""CA checker tests use generated disposable certificates only."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools/ops_check_ca.sh"


def test_ca_checker_validates_without_dumping_certificate(tmp_path):
    cert = tmp_path / "fake.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(tmp_path / "fake.key"),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=offline-fixture",
        ],
        check=True,
        capture_output=True,
    )
    cert.chmod(0o600)
    result = subprocess.run(["bash", str(SCRIPT), str(cert)], capture_output=True, text=True)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert "Fingerprint=" in lines[0]
    assert lines[1].startswith("notAfter=")
    assert "BEGIN" not in result.stdout
    cert.chmod(0o644)
    result = subprocess.run(["bash", str(SCRIPT), str(cert)], capture_output=True, text=True)
    assert result.returncode != 0
    assert not result.stdout


def test_ca_checker_missing_invalid_and_symlink(tmp_path):
    cert = tmp_path / "fake.crt"
    for contents in (None, "not a certificate"):
        if contents is not None:
            cert.write_text(contents)
            cert.chmod(0o600)
        result = subprocess.run(["bash", str(SCRIPT), str(cert)], capture_output=True)
        assert result.returncode != 0
        assert not result.stdout
    link = tmp_path / "link.crt"
    link.symlink_to(cert)
    assert subprocess.run(["bash", str(SCRIPT), str(link)], capture_output=True).returncode != 0
