from __future__ import annotations

# A64 top-level encoding groups, ARM ARM "A64 instruction set encoding":
# op0 = instruction bits [28:25]. Purely structural - this is where in the
# encoding space a word sits, not a claim about what it decodes to.
_GROUPS: tuple[tuple[str, str], ...] = (
    ("0000", "Reserved / SME"),
    ("0001", "Unallocated"),
    ("0010", "SVE"),
    ("0011", "Unallocated"),
    ("1000", "Data Processing -- Immediate"),
    ("1001", "Data Processing -- Immediate"),
    ("1010", "Branches, Exception, System"),
    ("1011", "Branches, Exception, System"),
    ("0100", "Loads and Stores"),
    ("0110", "Loads and Stores"),
    ("1100", "Loads and Stores"),
    ("1110", "Loads and Stores"),
    ("0101", "Data Processing -- Register"),
    ("1101", "Data Processing -- Register"),
    ("0111", "Data Processing -- Scalar FP / SIMD"),
    ("1111", "Data Processing -- Scalar FP / SIMD"),
)
_GROUP_MAP = {int(bits, 2): name for bits, name in _GROUPS}


def parse_word(text: str) -> int | None:
    t = text.strip().lower().replace("_", "")
    if not t:
        return None
    # a bare 32-digit run of 0s and 1s is meant as binary; everything else
    # is hex, with or without the 0x.
    if len(t) == 32 and set(t) <= {"0", "1"}:
        value = int(t, 2)
    else:
        try:
            value = int(t, 16)
        except ValueError:
            return None
    return value if 0 <= value <= 0xFFFFFFFF else None


def group_of(word: int) -> str:
    return _GROUP_MAP.get((word >> 25) & 0xF, "Unallocated")


def bit_rows(word: int) -> tuple[str, str]:
    bits = format(word & 0xFFFFFFFF, "032b")
    grouped = " ".join(bits[i : i + 4] for i in range(0, 32, 4))
    ruler_cells = []
    for i in range(0, 32, 4):
        hi = 31 - i
        ruler_cells.append(f"{hi:<2d}{'':<2}")
    ruler = " ".join(cell.rstrip().ljust(4) for cell in ruler_cells)
    return grouped, ruler


def hex_word(word: int) -> str:
    return f"0x{word & 0xFFFFFFFF:08x}"


def bytes_le(word: int) -> str:
    return " ".join(f"{(word >> (8 * i)) & 0xFF:02x}" for i in range(4))
