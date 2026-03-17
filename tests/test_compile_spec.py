from __future__ import annotations

import pytest

from pysilica.spec import compile as compile_mod

RET_WORD = 0xD65F03C0


def _real_xml_dir() -> str | None:
    try:
        manifest = compile_mod.load_spec_manifest()
    except FileNotFoundError:
        return None
    xml_dir = compile_mod._expand(manifest["xml_dir"])
    return str(xml_dir) if xml_dir.exists() else None


@pytest.mark.skipif(_real_xml_dir() is None, reason="vendor spec XML not present on this machine")
def test_compile_spec_against_real_vendor_corpus():
    manifest = compile_mod.load_spec_manifest()
    xml_dir = _real_xml_dir()
    assert xml_dir is not None
    result = compile_mod.compile_spec(xml_dir, manifest["release"])
    m = result.metrics
    assert m["ret_test_passed"] is True
    assert m["ret_test_word"] == hex(RET_WORD)
    assert m["tiling_files_checked"] == m["tiling_files_passed"]
    assert m["tiling_files_checked"] > 0
    assert m["allocated"] + m["unallocated"] == 2**32
    assert m["spec_release"] == manifest["release"]
