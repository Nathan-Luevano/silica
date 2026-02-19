from __future__ import annotations

import json
import os

from pysilica.verify.g1_spec_oracle import RET_WORD, verify_g1_spec_oracle


def test_fails_closed_with_no_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = verify_g1_spec_oracle()
    assert result.passed is False
    assert "missing" in result.evidence


def test_fails_on_broken_tiling_fixture(tmp_path, monkeypatch):
    # deliberately broken fixture: metrics claim a mismatch between files
    # checked and files passed, per DESIGN-FINAL.md §9's "ships with a
    # deliberately broken fixture" requirement.
    monkeypatch.chdir(tmp_path)
    os.makedirs("artifacts")
    (tmp_path / "artifacts" / "decode-table.bin").write_bytes(b"\x00")
    (tmp_path / "artifacts" / "g1_metrics.json").write_text(
        json.dumps(
            {
                "spec_release": "ISA_A64_xml_A_profile-2026-06_mc",
                "tiling_files_checked": 2267,
                "tiling_files_passed": 2266,
                "allocated": 1,
                "unallocated": 2**32 - 1,
                "ret_test_word": hex(RET_WORD),
                "ret_test_passed": True,
            }
        )
    )
    result = verify_g1_spec_oracle()
    assert result.passed is False
    assert result.measured["tiling_files_checked"] == 2267


def test_passes_on_correct_fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("artifacts")
    (tmp_path / "artifacts" / "decode-table.bin").write_bytes(b"\x00")
    (tmp_path / "artifacts" / "g1_metrics.json").write_text(
        json.dumps(
            {
                "spec_release": "ISA_A64_xml_A_profile-2026-06_mc",
                "tiling_files_checked": 2267,
                "tiling_files_passed": 2267,
                "allocated": 1000,
                "unallocated": 2**32 - 1000,
                "ret_test_word": hex(RET_WORD),
                "ret_test_passed": True,
            }
        )
    )
    result = verify_g1_spec_oracle()
    assert result.passed is True
