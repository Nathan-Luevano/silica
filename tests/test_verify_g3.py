from __future__ import annotations

import json
import os

from pysilica.verify.g3_normalization import verify_g3_normalization


def _seed(tmp_path, rule_counts, test_contents):
    os.makedirs(tmp_path / "tests" / "normalization")
    for name, content in test_contents.items():
        (tmp_path / "tests" / "normalization" / name).write_text(content)
    os.makedirs(tmp_path / "artifacts")
    (tmp_path / "artifacts" / "normalization_rule_counts.json").write_text(
        json.dumps(
            {
                "total_comparisons": 10,
                "collapsed_disagreements": 5,
                "rule_counts": rule_counts,
            }
        )
    )


def test_fails_closed_with_no_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = verify_g3_normalization()
    assert result.passed is False


def test_fails_without_alias_rule(tmp_path, monkeypatch):
    # deliberately broken fixture: every rule has a test, but none of them
    # is alias handling - this is the actual state of the real repo right
    # now, and must fail per §7's alias_list requirement.
    monkeypatch.chdir(tmp_path)
    _seed(
        tmp_path,
        rule_counts={"lowercase": 3, "whitespace": 2},
        test_contents={
            "test_lowercase.py": "def test_lowercase(): pass\n",
            "test_whitespace.py": "def test_whitespace(): pass\n",
        },
    )
    result = verify_g3_normalization()
    assert result.passed is False
    assert "alias" in str(result.evidence).lower()


def test_fails_with_untested_rule(tmp_path, monkeypatch):
    # deliberately broken fixture: alias rule exists in counts but no test
    # file mentions it - §7 rule 1 requires a test pinning every rule.
    monkeypatch.chdir(tmp_path)
    _seed(
        tmp_path,
        rule_counts={"alias_collapse": 4, "lowercase": 3},
        test_contents={"test_lowercase.py": "def test_lowercase(): pass\n"},
    )
    result = verify_g3_normalization()
    assert result.passed is False
    assert "alias_collapse" in result.evidence["untested_rules"]


def test_passes_on_correct_fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed(
        tmp_path,
        rule_counts={"alias_collapse": 4, "lowercase": 3},
        test_contents={
            "test_alias.py": "def test_alias_collapse_uses_spec_list(): pass\n",
            "test_lowercase.py": "def test_lowercase(): pass\n",
        },
    )
    result = verify_g3_normalization()
    assert result.passed is True


def test_fails_against_real_repo_state_pending_alias_handling():
    # the actual artifacts committed by P2 right now have no alias rule -
    # this pins that G3 correctly stays red until that's fixed, rather than
    # silently passing something incomplete.
    result = verify_g3_normalization()
    assert result.passed is False
