from __future__ import annotations

from pysilica.spec import mra

RET_XML = """<?xml version="1.0"?>
<instructionsection id="RET" type="instruction">
  <docvars>
    <docvar key="mnemonic" value="RET"/>
  </docvars>
  <classes>
    <iclass name="Integer" id="iclass_integer">
      <arch_variants>
        <arch_variant feature="FEAT_GCS" name="v9.4"/>
      </arch_variants>
      <regdiagram form="32" psname="A64.control.branch_reg.RET_64R_branch_reg">
        <box hibit="31" width="3" settings="3"><c>1</c><c>1</c><c>0</c></box>
        <box hibit="28" width="3" settings="3"><c>1</c><c>0</c><c>1</c></box>
        <box hibit="25" width="1" settings="1"><c>1</c></box>
        <box hibit="24" name="Z" settings="1"><c>0</c></box>
        <box hibit="23" width="1" settings="1"><c>0</c></box>
        <box hibit="22" width="2" name="op" settings="2"><c>1</c><c>0</c></box>
        <box hibit="20" width="5" name="op2" settings="5"><c>1</c><c>1</c><c>1</c><c>1</c><c>1</c></box>
        <box hibit="15" width="4" settings="4"><c>0</c><c>0</c><c>0</c><c>0</c></box>
        <box hibit="11" name="A" settings="1"><c>0</c></box>
        <box hibit="10" name="M" settings="1"><c>0</c></box>
        <box hibit="9" width="5" name="Rn"><c colspan="5"/></box>
        <box hibit="4" width="5" name="Rm" settings="5"><c>0</c><c>0</c><c>0</c><c>0</c><c>0</c></box>
      </regdiagram>
      <encoding name="RET_64R_branch_reg"/>
    </iclass>
  </classes>
  <commit_id>2026-06_rel</commit_id>
</instructionsection>
"""

RET_WORD = 0xD65F03C0


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


def test_ret_boxes_tile_32_bits(tmp_path):
    path = _write(tmp_path, "ret.xml", RET_XML)
    parsed = mra.parse_file(str(path))
    assert parsed is not None
    assert len(parsed.tilings) == 1
    assert parsed.tilings[0].ok is True
    assert sum(b.width for b in parsed.forms[0].boxes) == 32


def test_ret_word_matches_fixed_bits():
    # bits 31-29=110 28-26=101 25=1 24=0 23=0 22-21=10 20-16=11111
    # 15-12=0000 11=0 10=0 9-5=Rn(variable) 4-0=00000, Rn=30 -> 0xD65F03C0
    assert RET_WORD == (0xD65F0000 | (30 << 5))


def test_ret_parses_psname_and_gating():
    path_content = RET_XML
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = _write(__import__("pathlib").Path(d), "ret.xml", path_content)
        parsed = mra.parse_file(str(path))
    assert parsed is not None
    assert parsed.commit_id == "2026-06_rel"
    form = parsed.forms[0]
    assert form.psname == "A64.control.branch_reg.RET_64R_branch_reg"
    assert form.gating_features == ("FEAT_GCS",)
    assert form.encoding_name == "RET_64R_branch_reg"


def test_ret_word_matches_boxes_bit_by_bit(tmp_path):
    path = _write(tmp_path, "ret.xml", RET_XML)
    parsed = mra.parse_file(str(path))
    assert parsed is not None
    form = parsed.forms[0]
    word = RET_WORD
    for box in form.boxes:
        if box.fixed_bits is None:
            continue
        for i, ch in enumerate(box.fixed_bits):
            pos = box.hibit - i
            bit = (word >> pos) & 1
            assert bit == int(ch), f"bit {pos} expected {ch} got {bit}"


def test_width_absent_defaults_to_one(tmp_path):
    # RET_XML's Z/A/M boxes carry no width attribute at all
    path = _write(tmp_path, "ret.xml", RET_XML)
    parsed = mra.parse_file(str(path))
    assert parsed is not None
    z_box = next(b for b in parsed.forms[0].boxes if b.name == "Z")
    assert z_box.width == 1


def test_tiling_detects_gap():
    from pysilica.model import Box

    boxes = (
        Box(hibit=31, width=31, name=None, fixed_bits="1" * 31),
        # missing bit 0 -> gap
    )
    assert mra.tiling_ok(boxes) is False


def test_tiling_detects_overlap():
    from pysilica.model import Box

    boxes = (
        Box(hibit=31, width=16, name=None, fixed_bits="0" * 16),
        Box(hibit=20, width=21, name=None, fixed_bits="1" * 21),  # overlaps
    )
    assert mra.tiling_ok(boxes) is False


def test_undefined_keyword_scan_true():
    # simulate a Decode pstext containing UNPREDICTABLE
    xml = RET_XML.replace(
        "</instructionsection>",
        '<ps_section><ps name="x"><pstext section="Decode">if x then UNPREDICTABLE;</pstext></ps></ps_section></instructionsection>',
    )
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as d:
        path = _write(_Path(d), "ret_undef.xml", xml)
        parsed = mra.parse_file(str(path))
    assert parsed is not None
    assert parsed.has_decode_time_undefined is True


def test_non_instructionsection_root_skipped(tmp_path):
    path = _write(tmp_path, "index.xml", "<encodingindex/>")
    assert mra.parse_file(str(path)) is None
