from __future__ import annotations

import json
from pathlib import Path

from pysilica.analyze.rule_counts import generate_rule_counts


def test_generate_rule_counts(tmp_path: Path) -> None:
    out = tmp_path / "counts.json"
    data = generate_rule_counts(str(out))
    assert out.exists()
    loaded = json.loads(out.read_text())
    assert loaded["total_comparisons"] == 10
    assert loaded["collapsed_disagreements"] > 0
    assert "rule_counts" in loaded
    assert data == loaded
