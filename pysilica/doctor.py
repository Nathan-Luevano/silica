from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def load_manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(Path("manifests/spec.yml").read_text())
    return loaded


def check_spec_paths(manifest: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    spec = manifest["spec"]
    path = _expand(spec["path"])
    xml_dir = _expand(spec.get("xml_dir", spec["path"]))
    checks.append(Check("spec.path exists", path.exists(), str(path)))
    checks.append(Check("spec.xml_dir exists", xml_dir.exists(), str(xml_dir)))

    tarball_candidates = list(path.parent.glob(f"{spec['release']}.tar.gz"))
    if not tarball_candidates:
        checks.append(Check("spec tarball found", False, f"no {spec['release']}.tar.gz next to {path}"))
    else:
        tarball = tarball_candidates[0]
        actual = hashlib.sha256(tarball.read_bytes()).hexdigest()
        ok = actual == spec["sha256"]
        checks.append(Check("spec tarball sha256 matches", ok, f"{actual} vs manifest {spec['sha256']}"))

    if xml_dir.exists():
        sample = list(xml_dir.glob("*.xml"))[:20]
        mismatches = [
            f.name for f in sample if f"<commit_id>{spec['commit_id']}</commit_id>" not in f.read_text()
        ]
        checks.append(
            Check(
                "sampled commit_id matches spec.commit_id",
                len(sample) > 0 and not mismatches,
                f"checked {len(sample)}, mismatched {mismatches}",
            )
        )
    return checks


def check_capstone() -> Check:
    try:
        import capstone  # noqa: F401

        return Check("capstone importable", True, "python binding present")
    except ImportError as e:
        return Check("capstone importable", False, str(e))


def check_llvm() -> Check:
    result = subprocess.run(["llvm-config", "--libdir"], capture_output=True, text=True, check=False)
    return Check("llvm-config present", result.returncode == 0, result.stdout.strip() or result.stderr.strip())


def check_unicorn() -> Check:
    try:
        import unicorn  # noqa: F401

        return Check("unicorn importable", True, "python binding present")
    except ImportError as e:
        return Check("unicorn importable", False, str(e))


def check_binutils_aarch64() -> Check:
    result = subprocess.run(["objdump", "--info"], capture_output=True, text=True, check=False)
    has_target = "aarch64" in result.stdout
    return Check("objdump has aarch64 target", has_target, "objdump --info | grep aarch64")


def check_ghidra(manifest: dict[str, Any]) -> Check:
    ghidra_path = _expand(manifest["tools"]["ghidra"]["path"])
    headless = ghidra_path / "support" / "analyzeHeadless"
    return Check("ghidra analyzeHeadless present", headless.exists(), str(headless))


def run_all() -> list[Check]:
    manifest = load_manifest()
    checks: list[Check] = []
    checks.extend(check_spec_paths(manifest))
    checks.append(check_capstone())
    checks.append(check_llvm())
    checks.append(check_unicorn())
    checks.append(check_binutils_aarch64())
    checks.append(check_ghidra(manifest))
    return checks
