from __future__ import annotations

import glob
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from pysilica.model import AliasRef, Box, InstructionForm

EXPECTED_COMMIT_ID = "2026-06_rel"
DECODE_SECTIONS = ("Decode", "Postdecode")
# design.md §1.1 non-goals: "No SVE / SVE2 / SME... Base A64 plus Advanced
# SIMD only." "mortlach"/"mortlach2" are ARM's internal codenames for SME/
# SME2 in this XML release (confirmed by content: ZA tile regs, MOPA/MOPS
# outer-product instructions, MOVA tile-to-vector) - not obvious from the
# name alone, so allowlisting known-good classes rather than blocklisting
# guessed-bad ones, in case a future spec release adds another SVE/SME
# codename we haven't seen.
IN_SCOPE_INSTR_CLASSES = frozenset({"general", "advsimd", "float", "fpsimd", "system"})
UNDEFINED_KEYWORDS = (
    "UNDEFINED",
    "UNPREDICTABLE",
    "UnallocatedEncoding",
    "EndOfInstruction",
    "ReservedEncoding",
)


@dataclass(frozen=True)
class TilingCheck:
    file: str
    iclass_id: str
    ok: bool


@dataclass(frozen=True)
class ParsedFile:
    path: str
    commit_id: str | None
    instr_type: str | None
    instr_class: str | None
    forms: tuple[InstructionForm, ...]  # only in-scope type="instruction" iclasses
    tilings: tuple[TilingCheck, ...]  # every iclass regardless of type or scope
    has_decode_time_undefined: bool
    out_of_scope: bool  # true when instr_class is SVE/SVE2/SME/SME2 etc
    alias_refs: tuple[AliasRef, ...] = ()


def parse_boxes(regdiagram: ET.Element) -> tuple[Box, ...]:
    boxes = []
    for box in regdiagram.findall("box"):
        hibit = int(box.get("hibit", "0"))
        width = int(box.get("width", "1"))
        name = box.get("name")
        cs = box.findall("c")
        if not cs:
            fixed_bits = None
        else:
            # a box's <c> children are either all fixed (0/1) or all empty
            # (unconstrained) in the real corpus -- never mixed, checked by
            # a one-off scan over every box in the 2267-file corpus.
            variable = any((c.text or "").strip() not in ("0", "1") for c in cs)
            fixed_bits = None if variable else "".join((c.text or "").strip() for c in cs)
        boxes.append(Box(hibit=hibit, width=width, name=name, fixed_bits=fixed_bits))
    return tuple(boxes)


def tiling_ok(boxes: tuple[Box, ...]) -> bool:
    # free invariant, DESIGN-FINAL.md §5.2: widths sum to 32, no gap, no overlap
    ordered = sorted(boxes, key=lambda b: -b.hibit)
    expect_hibit = 31
    for box in ordered:
        if box.hibit != expect_hibit:
            return False
        expect_hibit = box.hibit - box.width
    return expect_hibit == -1


def _text_of(elem: ET.Element) -> str:
    return "".join(elem.itertext())


def _pstext_has_undefined(root: ET.Element) -> bool:
    for pstext in root.iter("pstext"):
        if pstext.get("section") in DECODE_SECTIONS:
            txt = _text_of(pstext)
            if any(kw in txt for kw in UNDEFINED_KEYWORDS):
                return True
    return False


def _mnemonic_of(root: ET.Element) -> str:
    for docvar in root.iter("docvar"):
        if docvar.get("key") == "mnemonic":
            value = docvar.get("value")
            if value:
                return value
    return root.get("id", "")


def _instr_class_of(root: ET.Element) -> str | None:
    for docvar in root.iter("docvar"):
        if docvar.get("key") == "instr-class":
            return docvar.get("value")
    return None


def _gating_features(iclass: ET.Element) -> tuple[str, ...]:
    features = []
    for variant in iclass.iter("arch_variant"):
        feature = variant.get("feature")
        if feature:
            features.append(feature)
    return tuple(features)


def _encoding_names(iclass: ET.Element) -> str:
    names = [enc.get("name", "") for enc in iclass.findall("encoding")]
    return "|".join(n for n in names if n)


def parse_file(path: str) -> ParsedFile | None:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    if root.tag != "instructionsection":
        return None

    instr_type = root.get("type")
    instr_class = _instr_class_of(root)
    out_of_scope = instr_type == "instruction" and instr_class not in IN_SCOPE_INSTR_CLASSES
    commit = root.find("commit_id")
    commit_id = commit.text if commit is not None else None
    mnemonic = _mnemonic_of(root)
    has_undef = instr_type == "instruction" and _pstext_has_undefined(root)

    forms = []
    tilings = []
    for iclass in root.findall("./classes/iclass"):
        regdiagram = iclass.find("regdiagram")
        if regdiagram is None:
            continue
        boxes = parse_boxes(regdiagram)
        ok = tiling_ok(boxes)
        # tiling is a parser-correctness check (design.md §5.2's "free
        # invariant") and applies regardless of scope - an SVE file with a
        # malformed regdiagram is still a parser bug worth catching, even
        # though its forms never enter the decode tree below.
        tilings.append(TilingCheck(file=path, iclass_id=iclass.get("id", ""), ok=ok))
        if instr_type != "instruction" or out_of_scope:
            continue
        psname = regdiagram.get("psname")
        if not psname:
            continue
        forms.append(
            InstructionForm(
                iclass_id=iclass.get("id", ""),
                encoding_name=_encoding_names(iclass),
                psname=psname,
                mnemonic=mnemonic,
                boxes=boxes,
                gating_features=_gating_features(iclass),
            )
        )

    alias_refs = []
    if instr_type == "instruction" and not out_of_scope:
        for aref in root.findall(".//aliasref"):
            page_id = aref.get("aliaspageid", "")
            afile = aref.get("aliasfile", "")
            text_elem = aref.find("text")
            text = text_elem.text.strip() if text_elem is not None and text_elem.text else ""
            pref_elem = aref.find("aliaspref")
            pref = pref_elem.text.strip() if pref_elem is not None and pref_elem.text else ""
            alias_mnem = text.split()[0] if text else page_id.split("_")[0]
            alias_refs.append(
                AliasRef(
                    base_mnemonic=mnemonic,
                    alias_mnemonic=alias_mnem,
                    alias_name=text,
                    alias_page_id=page_id,
                    alias_file=afile,
                    condition=pref,
                )
            )

    return ParsedFile(
        path=path,
        commit_id=commit_id,
        instr_type=instr_type,
        instr_class=instr_class,
        forms=tuple(forms),
        tilings=tuple(tilings),
        has_decode_time_undefined=has_undef,
        out_of_scope=out_of_scope,
        alias_refs=tuple(alias_refs),
    )


def iter_xml_files(xml_dir: str | os.PathLike[str]) -> list[str]:
    return sorted(glob.glob(str(Path(xml_dir) / "*.xml")))


def parse_all(xml_dir: str | os.PathLike[str]) -> list[ParsedFile]:
    parsed = []
    for path in iter_xml_files(xml_dir):
        pf = parse_file(path)
        if pf is not None:
            parsed.append(pf)
    return parsed
