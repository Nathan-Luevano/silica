from __future__ import annotations

from pysilica.model import Box, InstructionForm
from pysilica.spec import tables


def _form(psname, boxes, mnemonic="X", gating=()):
    return InstructionForm(
        iclass_id="ic",
        encoding_name=psname,
        psname=psname,
        mnemonic=mnemonic,
        boxes=boxes,
        gating_features=gating,
    )


def test_single_form_all_fixed_bits_allocated_count_is_one():
    boxes = (Box(hibit=31, width=32, name=None, fixed_bits="0" * 32),)
    form = _form("p.only", boxes)
    tree = tables.build_tree([form], "rel")
    assert tables.count_allocated(tree) == 1
    matched, ambiguous = tables.classify(tree, 0)
    assert matched is not None and matched.psname == "p.only"
    assert ambiguous == 1
    matched, _ = tables.classify(tree, 1)
    assert matched is None


def test_ret_like_form_allocated_count_matches_free_bits():
    # bits 31-10: 22 fixed bits. bits 9-5: Rn, free. bits 4-0: Rm, fixed 00000.
    fixed_hi = "110" + "101" + "1" + "0" + "0" + "10" + "11111" + "0000" + "0" + "0"
    assert len(fixed_hi) == 22
    boxes = (
        Box(hibit=31, width=22, name=None, fixed_bits=fixed_hi),
        Box(hibit=9, width=5, name="Rn", fixed_bits=None),
        Box(hibit=4, width=5, name="Rm", fixed_bits="00000"),
    )
    form = _form("A64.control.branch_reg.RET_64R_branch_reg", boxes)
    tree = tables.build_tree([form], "rel")
    assert tables.count_allocated(tree) == 2**5
    ret_word = 0xD65F03C0
    matched, ambiguous = tables.classify(tree, ret_word)
    assert matched is not None
    assert matched.psname == "A64.control.branch_reg.RET_64R_branch_reg"
    assert ambiguous == 1


def test_two_disjoint_forms_sum_counts():
    f1 = _form("p1", (Box(hibit=31, width=32, name=None, fixed_bits="0" + "0" * 31),))
    f2 = _form("p2", (Box(hibit=31, width=1, name=None, fixed_bits="1"), Box(hibit=30, width=31, name=None, fixed_bits=None)))
    tree = tables.build_tree([f1, f2], "rel")
    assert tables.count_allocated(tree) == 1 + 2**31
    assert tables.ambiguous_leaf_groups(tree) == 0


def test_overlapping_forms_flagged_ambiguous():
    boxes = (Box(hibit=31, width=32, name=None, fixed_bits="1" * 32),)
    f1 = _form("p1", boxes)
    f2 = _form("p2", boxes)
    tree = tables.build_tree([f1, f2], "rel")
    assert tables.count_allocated(tree) == 1
    assert tables.ambiguous_leaf_groups(tree) == 1
    _matched, ambiguous = tables.classify(tree, 0xFFFFFFFF)
    assert ambiguous == 2


def test_roundtrip_through_bytes(tmp_path):
    boxes = (Box(hibit=31, width=32, name=None, fixed_bits="0" * 32),)
    form = _form("p.rt", boxes, mnemonic="RT", gating=("FEAT_X", "FEAT_Y"))
    tree = tables.build_tree([form], "ISA_A64_xml_A_profile-2026-06_mc")
    path = str(tmp_path / "dt.bin")
    tables.write_decode_table(tree, path)
    loaded = tables.read_decode_table(path)
    assert loaded.spec_release == "ISA_A64_xml_A_profile-2026-06_mc"
    assert loaded.forms[0].psname == "p.rt"
    assert loaded.forms[0].gating_features == ("FEAT_X", "FEAT_Y")
    matched, ambiguous = tables.classify(loaded, 0)
    assert matched is not None and matched.psname == "p.rt"
    assert ambiguous == 1
    assert tables.count_allocated(loaded) == 1
