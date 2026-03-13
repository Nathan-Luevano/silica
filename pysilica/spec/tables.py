from __future__ import annotations

import struct
from dataclasses import dataclass

from pysilica.model import InstructionForm

MAGIC = b"SIL1"

KIND_INTERNAL = 0
KIND_ALLOCATED = 1
KIND_UNALLOCATED = 2


@dataclass(frozen=True)
class Node:
    kind: int
    bit: int
    a: int
    b: int


@dataclass(frozen=True)
class DecodeTree:
    spec_release: str
    forms: tuple[InstructionForm, ...]
    nodes: tuple[Node, ...]
    root_index: int


def _bitkind(form: InstructionForm) -> list[str | None]:
    # index 0..31 == bit position, value '0'/'1' fixed or None variable
    kinds: list[str | None] = [None] * 32
    for box in form.boxes:
        for i in range(box.width):
            pos = box.hibit - i
            if 0 <= pos < 32:
                kinds[pos] = None if box.fixed_bits is None else box.fixed_bits[i]
    return kinds


def build_tree(forms: list[InstructionForm], spec_release: str) -> DecodeTree:
    bitkinds = [_bitkind(f) for f in forms]
    memo: dict[tuple[frozenset[int], int], int] = {}
    nodes: list[Node] = []

    def rec(idxs: frozenset[int], bit: int) -> int:
        key = (idxs, bit)
        cached = memo.get(key)
        if cached is not None:
            return cached
        if not idxs:
            node_idx = len(nodes)
            nodes.append(Node(KIND_UNALLOCATED, 0, 0, 0))
            memo[key] = node_idx
            return node_idx
        if bit < 0:
            # every fixed bit consulted; whichever forms remain all match
            # this exact word. Normally exactly one -- see formats.md's
            # ambiguous_leaf_groups for the (rare, measured) exceptions.
            node_idx = len(nodes)
            nodes.append(Node(KIND_ALLOCATED, 0, min(idxs), len(idxs)))
            memo[key] = node_idx
            return node_idx
        idxs0 = frozenset(i for i in idxs if bitkinds[i][bit] in ("0", None))
        idxs1 = frozenset(i for i in idxs if bitkinds[i][bit] in ("1", None))
        left = rec(idxs0, bit - 1)
        right = rec(idxs1, bit - 1)
        node_idx = len(nodes)
        nodes.append(Node(KIND_INTERNAL, bit, left, right))
        memo[key] = node_idx
        return node_idx

    root = rec(frozenset(range(len(forms))), 31)
    return DecodeTree(spec_release=spec_release, forms=tuple(forms), nodes=tuple(nodes), root_index=root)


def classify(tree: DecodeTree, word: int) -> tuple[InstructionForm | None, int]:
    idx = tree.root_index
    while True:
        node = tree.nodes[idx]
        if node.kind == KIND_UNALLOCATED:
            return None, 0
        if node.kind == KIND_ALLOCATED:
            return tree.forms[node.a], node.b
        bit_val = (word >> node.bit) & 1
        idx = node.b if bit_val else node.a


def count_allocated(tree: DecodeTree) -> int:
    cache: dict[int, int] = {}

    def rec(idx: int) -> int:
        cached = cache.get(idx)
        if cached is not None:
            return cached
        node = tree.nodes[idx]
        if node.kind == KIND_UNALLOCATED:
            v = 0
        elif node.kind == KIND_ALLOCATED:
            v = 1
        else:
            v = rec(node.a) + rec(node.b)
        cache[idx] = v
        return v

    return rec(tree.root_index)


def ambiguous_leaf_groups(tree: DecodeTree) -> int:
    return sum(1 for n in tree.nodes if n.kind == KIND_ALLOCATED and n.b > 1)


def _write_str(buf: bytearray, s: str) -> None:
    data = s.encode("utf-8")
    buf.extend(struct.pack("<I", len(data)))
    buf.extend(data)


def _read_str(data: bytes, offset: int) -> tuple[str, int]:
    (n,) = struct.unpack_from("<I", data, offset)
    offset += 4
    s = data[offset : offset + n].decode("utf-8")
    return s, offset + n


def write_decode_table(tree: DecodeTree, path: str) -> None:
    buf = bytearray()
    buf.extend(MAGIC)
    _write_str(buf, tree.spec_release)
    buf.extend(struct.pack("<I", len(tree.forms)))
    for form in tree.forms:
        _write_str(buf, form.psname)
        _write_str(buf, form.encoding_name)
        _write_str(buf, form.mnemonic)
        _write_str(buf, ",".join(form.gating_features))
    buf.extend(struct.pack("<I", len(tree.nodes)))
    for node in tree.nodes:
        buf.extend(struct.pack("<BBxxII", node.kind, node.bit, node.a, node.b))
    buf.extend(struct.pack("<I", tree.root_index))
    with open(path, "wb") as f:
        f.write(bytes(buf))


def read_decode_table(path: str) -> DecodeTree:
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != MAGIC:
        raise ValueError(f"bad magic in {path}: {data[:4]!r}")
    offset = 4
    spec_release, offset = _read_str(data, offset)
    (form_count,) = struct.unpack_from("<I", data, offset)
    offset += 4
    forms = []
    for _ in range(form_count):
        psname, offset = _read_str(data, offset)
        encoding_name, offset = _read_str(data, offset)
        mnemonic, offset = _read_str(data, offset)
        gating_str, offset = _read_str(data, offset)
        gating = tuple(g for g in gating_str.split(",") if g)
        forms.append(
            InstructionForm(
                iclass_id="",
                encoding_name=encoding_name,
                psname=psname,
                mnemonic=mnemonic,
                boxes=(),
                gating_features=gating,
            )
        )
    (node_count,) = struct.unpack_from("<I", data, offset)
    offset += 4
    nodes = []
    for _ in range(node_count):
        kind, bit, a, b = struct.unpack_from("<BBxxII", data, offset)
        offset += 12
        nodes.append(Node(kind=kind, bit=bit, a=a, b=b))
    (root_index,) = struct.unpack_from("<I", data, offset)
    offset += 4
    return DecodeTree(spec_release=spec_release, forms=tuple(forms), nodes=tuple(nodes), root_index=root_index)
