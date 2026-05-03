from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pysilica.model import AliasRef, InstructionForm
from pysilica.spec import mra, tables

RET_WORD = 0xD65F03C0


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def load_spec_manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(Path("manifests/spec.yml").read_text())
    return dict(loaded["spec"])


@dataclass(frozen=True)
class CompileResult:
    tree: tables.DecodeTree
    metrics: dict[str, object]
    aliases: dict[str, list[dict[str, str]]]


def compile_spec(xml_dir: str | os.PathLike[str], spec_release: str) -> CompileResult:
    parsed = mra.parse_all(xml_dir)

    forms: list[InstructionForm] = []
    alias_refs: list[AliasRef] = []
    tiling_files_checked = 0
    tiling_files_passed = 0
    instruction_files = 0
    alias_files = 0
    decode_time_undefined_forms = 0
    out_of_scope_files = 0
    out_of_scope_classes: dict[str, int] = {}

    for pf in parsed:
        if pf.instr_type not in ("instruction", "alias"):
            continue
        if pf.instr_type == "instruction":
            instruction_files += 1
            if pf.has_decode_time_undefined:
                decode_time_undefined_forms += 1
            if pf.out_of_scope:
                out_of_scope_files += 1
                cls = pf.instr_class or "unknown"
                out_of_scope_classes[cls] = out_of_scope_classes.get(cls, 0) + 1
            alias_refs.extend(pf.alias_refs)
        else:
            alias_files += 1
        if not pf.tilings:
            continue
        tiling_files_checked += 1
        if all(t.ok for t in pf.tilings):
            tiling_files_passed += 1
        # pf.forms is already scope-filtered in mra.parse_file (design.md
        # §1.1: no SVE/SVE2/SME in v1) - out-of-scope files still get
        # box-tiling checked above, just never feed the decode tree.
        forms.extend(pf.forms)

    tree = tables.build_tree(forms, spec_release)
    allocated = tables.count_allocated(tree)
    unallocated = (2**32) - allocated

    matched, ambiguous = tables.classify(tree, RET_WORD)
    ret_test_passed = (
        matched is not None
        and matched.psname == "A64.control.branch_reg.RET_64R_branch_reg"
        and ambiguous == 1
    )

    aliases_dict: dict[str, list[dict[str, str]]] = {}
    for aref in alias_refs:
        base = aref.base_mnemonic.upper()
        if base not in aliases_dict:
            aliases_dict[base] = []
        aliases_dict[base].append({
            "alias_mnemonic": aref.alias_mnemonic.upper(),
            "alias_name": aref.alias_name,
            "alias_page_id": aref.alias_page_id,
            "alias_file": aref.alias_file,
            "condition": aref.condition,
        })

    metrics: dict[str, object] = {
        "spec_release": spec_release,
        "tiling_files_checked": tiling_files_checked,
        "tiling_files_passed": tiling_files_passed,
        "allocated": allocated,
        "unallocated": unallocated,
        "ret_test_word": hex(RET_WORD),
        "ret_test_passed": ret_test_passed,
        "decode_time_undefined_forms": decode_time_undefined_forms,
        "ambiguous_leaf_groups": tables.ambiguous_leaf_groups(tree),
        "instruction_files": instruction_files,
        "alias_files": alias_files,
        "out_of_scope_files": out_of_scope_files,
        "out_of_scope_classes": out_of_scope_classes,
        "form_count": len(forms),
        "node_count": len(tree.nodes),
        "in_scope_alias_count": len(alias_refs),
    }
    return CompileResult(tree=tree, metrics=metrics, aliases=aliases_dict)


def run_and_write(artifacts_dir: str = "artifacts") -> CompileResult:
    manifest = load_spec_manifest()
    xml_dir = _expand(manifest["xml_dir"])
    result = compile_spec(xml_dir, manifest["release"])

    out_dir = Path(artifacts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables.write_decode_table(result.tree, str(out_dir / "decode-table.bin"))
    (out_dir / "g1_metrics.json").write_text(json.dumps(result.metrics, indent=2) + "\n")
    (out_dir / "spec_aliases.json").write_text(json.dumps(result.aliases, indent=2) + "\n")
    return result
