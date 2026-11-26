"""CA checker tests use generated disposable certificates only."""

import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools/ops_check_ca.sh"
CHECKS = [
    "exists: PASS",
    "not a symlink: PASS",
    "mode 600: PASS",
    "parses with openssl x509: PASS",
    "not expired: PASS",
]


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
    assert lines[:5] == CHECKS
    assert len(lines) == 7
    assert "Fingerprint=" in lines[5]
    assert lines[6].startswith("notAfter=")
    assert "BEGIN" not in result.stdout
    cert.chmod(0o644)
    result = subprocess.run(["bash", str(SCRIPT), str(cert)], capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        *CHECKS[:2],
        "mode 600: FAIL",
        *CHECKS[3:],
    ]
    assert "Fingerprint=" not in result.stdout
    assert "notAfter=" not in result.stdout


def test_ca_checker_missing_invalid_and_symlink(tmp_path):
    cert = tmp_path / "fake.crt"
    result = subprocess.run(["bash", str(SCRIPT), str(cert)], capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        "exists: FAIL",
        "not a symlink: PASS",
        "mode 600: FAIL",
        "parses with openssl x509: FAIL",
        "not expired: FAIL",
    ]
    cert.write_text("not a certificate")
    cert.chmod(0o600)
    result = subprocess.run(["bash", str(SCRIPT), str(cert)], capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        *CHECKS[:3],
        "parses with openssl x509: FAIL",
        "not expired: FAIL",
    ]
    assert not result.stderr
    link = tmp_path / "link.crt"
    link.symlink_to(cert)
    result = subprocess.run(["bash", str(SCRIPT), str(link)], capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout.splitlines()[1] == "not a symlink: FAIL"
    assert "Fingerprint=" not in result.stdout


def test_ca_checker_expired_certificate_reports_only_safe_checks(tmp_path):
    key = tmp_path / "fake.key"
    cert = tmp_path / "expired.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=offline-expired-fixture",
        ],
        check=True,
        capture_output=True,
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    wrapper = fake_bin / "openssl"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ " $* " == *" -checkend 0 "* ]]; then exit 1; fi\n'
        f'exec "{shutil.which("openssl")}" "$@"\n'
    )
    wrapper.chmod(0o700)
    cert.chmod(0o600)
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(SCRIPT), str(cert)], capture_output=True, text=True, env=environment
    )
    assert result.returncode != 0
    assert result.stdout.splitlines() == [*CHECKS[:4], "not expired: FAIL"]
    assert "Fingerprint=" not in result.stdout
    assert "notAfter=" not in result.stdout
    assert not result.stderr
