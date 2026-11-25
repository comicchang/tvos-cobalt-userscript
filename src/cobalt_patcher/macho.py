"""Clean-room ARM64 Mach-O binary patcher for Cobalt tvOS executables."""

from __future__ import annotations

import json
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lief
from capstone import CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, Cs

_MD = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
_MD.detail = False

# Register pairs spilled by hook trampolines (11 pairs = 0xB0 bytes).
_SPILL_PAIRS: tuple[tuple[int, int], ...] = (
    (0, 1),
    (2, 3),
    (4, 5),
    (6, 7),
    (8, 9),
    (10, 11),
    (12, 13),
    (14, 15),
    (16, 17),
    (18, 19),
    (29, 30),
)
_SPILL_BYTES = len(_SPILL_PAIRS) * 16


@dataclass(frozen=True)
class PatchRecord:
    name: str
    entry_va: int
    site_va: int
    stub_va: int
    stub_size: int


class BumpRegion:
    """Linear bump allocator inside Mach-O __TEXT header padding."""

    def __init__(self, start: int, limit: int) -> None:
        if start >= limit:
            raise ValueError(f"No padding space available ({hex(start)} >= {hex(limit)})")
        self.cursor = start
        self.limit = limit

    def reserve(self, size: int, align: int = 4) -> int:
        aligned = (self.cursor + align - 1) & ~(align - 1)
        if aligned + size > self.limit:
            raise ValueError(
                f"Header padding exhausted: need {size}B at {hex(aligned)}, limit {hex(self.limit)}"
            )
        self.cursor = aligned + size
        return aligned


def load_profile(name_or_path: str | Path) -> dict[str, Any]:
    """Load a built-in profile by name or a custom JSON profile path."""
    p = Path(name_or_path)
    if not p.is_file():
        builtin = Path(__file__).parent / "profiles" / f"{name_or_path}.json"
        if not builtin.is_file():
            raise FileNotFoundError(f"Unknown patch profile: {name_or_path}")
        p = builtin
    return json.loads(p.read_text(encoding="utf-8"))


def encode_arm64_bl(from_pc: int, to_va: int) -> int:
    """Encode an ARM64 PC-relative BL instruction."""
    delta = to_va - from_pc
    if delta % 4 != 0:
        raise ValueError("BL target must be 4-byte aligned")
    imm26 = delta >> 2
    if not (-(1 << 25) <= imm26 < (1 << 25)):
        raise ValueError(f"BL target out of +-128MB range: {hex(from_pc)} -> {hex(to_va)}")
    return 0x94000000 | (imm26 & 0x03FFFFFF)


def check_unencrypted(binary: lief.MachO.Binary) -> None:
    """Reject FairPlay-encrypted binaries (cryptid != 0) with a clear hint."""
    enc = binary.encryption_info
    if enc is not None and enc.crypt_id != 0:
        raise RuntimeError(
            f"Input binary is still FairPlay-encrypted (cryptid={enc.crypt_id}). "
            "Decrypt the IPA locally first before patching."
        )


def _assemble_arm64(lines: list[str]) -> bytes:
    """Assemble ARM64 assembly via Xcode's AppleTVOS clang and return __text bytes."""
    src = ".text\n.align 2\n" + "\n".join(lines) + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        s_file = Path(tmp) / "trampoline.s"
        o_file = Path(tmp) / "trampoline.o"
        s_file.write_text(src, encoding="ascii")
        cmd = [
            "xcrun",
            "--sdk",
            "appletvos",
            "clang",
            "-c",
            "-target",
            "arm64-apple-tvos",
            "-x",
            "assembler",
            "-o",
            str(o_file),
            str(s_file),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ARM64 assembly failed:\n{res.stderr}")
        obj = lief.parse(str(o_file))
        sec = obj.get_section("__text") if obj else None
        if sec is None:
            raise RuntimeError("Assembled object contains no __text section")
        return bytes(sec.content)


def _mov_imm64(reg: str, val: int) -> list[str]:
    return [
        f"movz {reg}, #0x{val & 0xFFFF:x}",
        f"movk {reg}, #0x{(val >> 16) & 0xFFFF:x}, lsl #16",
        f"movk {reg}, #0x{(val >> 32) & 0xFFFF:x}, lsl #32",
        f"movk {reg}, #0x{(val >> 48) & 0xFFFF:x}, lsl #48",
    ]


def _load_slid_va(reg: str, link_va: int, slide_reg: str = "x19") -> list[str]:
    return [*_mov_imm64(reg, link_va), f"add {reg}, {reg}, {slide_reg}"]


def _spill_regs() -> list[str]:
    asm = [f"sub sp, sp, #0x{_SPILL_BYTES:x}"]
    for idx, (r0, r1) in enumerate(_SPILL_PAIRS):
        asm.append(f"stp x{r0}, x{r1}, [sp, #0x{idx * 16:x}]")
    return asm


def _restore_regs() -> list[str]:
    asm: list[str] = []
    for idx in range(len(_SPILL_PAIRS) - 1, -1, -1):
        r0, r1 = _SPILL_PAIRS[idx]
        asm.append(f"ldp x{r0}, x{r1}, [sp, #0x{idx * 16:x}]")
    asm.append(f"add sp, sp, #0x{_SPILL_BYTES:x}")
    return asm


def _is_prologue_op(insn: Any) -> bool:
    m, o = insn.mnemonic, insn.op_str
    if m in {"pacibsp", "bti", "nop", "hint"}:
        return True
    if m == "sub" and o.startswith("sp, sp, #"):
        return True
    if m == "stp" and "[sp" in o:
        return True
    if m == "add" and o.startswith("x29, sp, #"):
        return True
    if m == "mov" and o == "x29, sp":
        return True
    return False


def _disasm_single(word_bytes: bytes, va: int) -> Any:
    insn = next(_MD.disasm(word_bytes, va), None)
    if insn is None:
        raise ValueError(f"Unable to disassemble ARM64 word at {hex(va)}")
    return insn


def _va_to_file_offset(text_va: int, text_off: int, text_size: int, va: int) -> int:
    off = text_off + (va - text_va)
    if off < text_off or off + 4 > text_off + text_size:
        raise ValueError(f"Virtual address outside __text: {hex(va)}")
    return off


def _find_post_prologue_site(
    data: bytearray,
    text_va: int,
    text_off: int,
    text_size: int,
    entry_va: int,
    max_insns: int = 24,
) -> tuple[int, int, int, Any]:
    first_off = _va_to_file_offset(text_va, text_off, text_size, entry_va)
    first = _disasm_single(bytes(data[first_off : first_off + 4]), entry_va)
    if not _is_prologue_op(first):
        raise ValueError(
            f"Expected prologue at {hex(entry_va)}, found '{first.mnemonic} {first.op_str}'"
        )
    for step in range(1, max_insns + 1):
        va = entry_va + step * 4
        off = _va_to_file_offset(text_va, text_off, text_size, va)
        insn = _disasm_single(bytes(data[off : off + 4]), va)
        if not _is_prologue_op(insn):
            raw = struct.unpack_from("<I", data, off)[0]
            return va, off, raw, insn
    raise ValueError(f"No post-prologue instruction found within {max_insns} ops of {hex(entry_va)}")


def _resolve_import_stub(binary: lief.MachO.Binary, symbol_name: str) -> int:
    stubs = binary.get_section("__stubs")
    dysym = binary.dynamic_symbol_command
    if stubs is None or dysym is None or stubs.reserved2 == 0:
        raise ValueError("Binary is missing __stubs or LC_DYSYMTAB")
    count = stubs.size // stubs.reserved2
    for i in range(count):
        sym = dysym.indirect_symbols[stubs.reserved1 + i]
        if getattr(sym, "name", None) == symbol_name:
            return stubs.virtual_address + i * stubs.reserved2
    raise ValueError(f"Required imported symbol stub not found: {symbol_name}")


def _build_trampoline(
    kind: str,
    stub_va: int,
    sym: dict[str, int],
    displaced_word: int,
    enable_logs: bool,
) -> bytes:
    label = f"tramp_{kind}"
    page_base = stub_va & ~0xFFF
    asm = [
        f"{label}:",
        *_spill_regs(),
        f"adr x17, {label}",
        "and x17, x17, #0xfffffffffffff000",
        *_mov_imm64("x16", page_base),
        "sub x19, x17, x16",
    ]

    if kind == "script_inject":
        asm += [
            "ldr x0, [sp, #0x08]",
            *_load_slid_va("x1", sym["needle"]),
            "mov x2, #0",
            *_mov_imm64("x3", sym["needle_len"]),
            *_load_slid_va("x16", sym["std_string_find"]),
            "blr x16",
            "cmn x0, #1",
            "b.eq skip_inject",
            "ldr x0, [sp, #0x08]",
            "mov x1, #0",
            *_load_slid_va("x2", sym["loader_js"]),
            *_mov_imm64("x3", sym["loader_js_len"]),
            *_load_slid_va("x16", sym["std_string_insert"]),
            "blr x16",
        ]
        if enable_logs:
            asm += [
                *_load_slid_va("x0", sym["log_script"]),
                *_load_slid_va("x16", sym["printf"]),
                "blr x16",
            ]
        asm.append("skip_inject:")

    elif kind == "csp_prepend":
        asm += [
            "ldr x0, [sp, #0x10]",
            "mov x1, #0",
            *_load_slid_va("x2", sym["csp"]),
            *_mov_imm64("x3", sym["csp_len"]),
            *_load_slid_va("x16", sym["std_string_insert"]),
            "blr x16",
        ]
        if enable_logs:
            asm += [
                *_load_slid_va("x0", sym["log_csp"]),
                *_load_slid_va("x16", sym["printf"]),
                "blr x16",
            ]

    elif kind == "vp9_profile2":
        asm += [
            "ldr x0, [sp]",
            "cbz x0, vp9_passthrough",
            *_load_slid_va("x1", sym["vp9_full"]),
            *_load_slid_va("x16", sym["strstr"]),
            "blr x16",
            "cbnz x0, vp9_check_hdr",
            "ldr x0, [sp]",
            *_load_slid_va("x1", sym["vp9_short"]),
            *_load_slid_va("x16", sym["strstr"]),
            "blr x16",
            "cbz x0, vp9_passthrough",
            "vp9_check_hdr:",
            *_load_slid_va("x0", sym["cls_avplayer"]),
            *_load_slid_va("x16", sym["objc_getClass"]),
            "blr x16",
            "cbz x0, vp9_passthrough",
            "mov x20, x0",
            "movn x0, #1",
            *_load_slid_va("x1", sym["sym_sel_register"]),
            *_load_slid_va("x16", sym["dlsym"]),
            "blr x16",
            "cbz x0, vp9_passthrough",
            "mov x16, x0",
            *_load_slid_va("x0", sym["sel_hdr_eligible"]),
            "blr x16",
            "cbz x0, vp9_passthrough",
            "mov x21, x0",
            "movn x0, #1",
            *_load_slid_va("x1", sym["sym_class_method"]),
            *_load_slid_va("x16", sym["dlsym"]),
            "blr x16",
            "cbz x0, vp9_passthrough",
            "mov x16, x0",
            "mov x0, x20",
            "mov x1, x21",
            "blr x16",
            "cbz x0, vp9_passthrough",
            "mov x0, x20",
            "mov x1, x21",
            *_load_slid_va("x16", sym["objc_msgSend"]),
            "blr x16",
            "tbz w0, #0, vp9_passthrough",
            *_restore_regs(),
            "mov w0, #2",
            "ldp x29, x30, [sp, #0x130]",
            "ldp x20, x19, [sp, #0x120]",
            "ldp x22, x21, [sp, #0x110]",
            "ldp x28, x27, [sp, #0x100]",
            "add sp, sp, #0x140",
            "ret",
            "vp9_passthrough:",
        ]
    else:
        raise ValueError(f"Unsupported hook kind: {kind}")

    asm += [*_restore_regs(), f".word 0x{displaced_word:08x}", "ret"]
    return _assemble_arm64(asm)


def patch_macho_bytes(
    raw_bytes: bytearray,
    profile: dict[str, Any],
    loader_js: bytes,
    csp_prefix: bytes,
    *,
    apply_hdr_patches: bool = True,
    enable_logs: bool = False,
) -> list[PatchRecord]:
    """Patch a decrypted arm64 tvOS Cobalt binary in place and return applied records."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_bin = Path(tmp) / "target.macho"
        tmp_bin.write_bytes(raw_bytes)
        binary = lief.parse(str(tmp_bin))

    if not isinstance(binary, lief.MachO.Binary):
        raise ValueError("Input executable is not a single-slice Mach-O binary")
    check_unencrypted(binary)

    text_sec = binary.get_section("__text")
    text_seg = binary.get_segment("__TEXT")
    if text_sec is None or text_seg is None:
        raise ValueError("Required __TEXT / __text sections missing")

    header_end = 32 + binary.header.sizeof_cmds
    pool = BumpRegion(header_end, text_sec.offset)

    def place(blob: bytes, align: int = 4) -> int:
        off = pool.reserve(len(blob), align)
        raw_bytes[off : off + len(blob)] = blob
        return text_seg.virtual_address + (off - text_seg.file_offset)

    needle = profile["script_needle"].encode("ascii")
    sym: dict[str, int] = {
        "needle": place(needle + b"\0"),
        "needle_len": len(needle),
        "loader_js": place(loader_js + b"\0"),
        "loader_js_len": len(loader_js),
        "csp": place(csp_prefix + b"\0"),
        "csp_len": len(csp_prefix),
        "std_string_find": _resolve_import_stub(
            binary, "__ZNKSt3__112basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEE4findEPKcmm"
        ),
        "std_string_insert": _resolve_import_stub(
            binary, "__ZNSt3__112basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEE6insertEmPKcm"
        ),
    }

    if apply_hdr_patches:
        sym.update(
            {
                "vp9_full": place(b"vp09.02\0"),
                "vp9_short": place(b"vp9.2\0"),
                "cls_avplayer": place(b"AVPlayer\0"),
                "sym_sel_register": place(b"sel_registerName\0"),
                "sym_class_method": place(b"class_getClassMethod\0"),
                "sel_hdr_eligible": place(b"eligibleForHDRPlayback\0"),
                "strstr": _resolve_import_stub(binary, "_strstr"),
                "objc_getClass": _resolve_import_stub(binary, "_objc_getClass"),
                "objc_msgSend": _resolve_import_stub(binary, "_objc_msgSend"),
                "dlsym": _resolve_import_stub(binary, "_dlsym"),
            }
        )

    if enable_logs:
        sym.update(
            {
                "log_script": place(b"[cobalt-us] script injected\n\0"),
                "log_csp": place(b"[cobalt-us] csp updated\n\0"),
                "printf": _resolve_import_stub(binary, "_printf"),
            }
        )

    pool.reserve(0, 16)

    hook_specs = list(profile.get("hooks", []))
    if apply_hdr_patches:
        hook_specs.extend(profile.get("media_hooks", []))

    resolved: list[dict[str, Any]] = []
    for spec in hook_specs:
        entry_va = int(spec["entry_va"], 16)
        site_va, site_off, raw_word, insn = _find_post_prologue_site(
            raw_bytes, text_sec.virtual_address, text_sec.offset, text_sec.size, entry_va
        )
        if (insn.mnemonic, insn.op_str) != (spec["expect_mnemonic"], spec["expect_op_str"]):
            raise ValueError(
                f"Hook '{spec['name']}' mismatch at {hex(site_va)}: "
                f"expected '{spec['expect_mnemonic']} {spec['expect_op_str']}', "
                f"got '{insn.mnemonic} {insn.op_str}'. Check your IPA version matches profile '{profile['name']}'."
            )
        resolved.append(
            {
                "name": spec["name"],
                "kind": spec["kind"],
                "entry_va": entry_va,
                "site_va": site_va,
                "site_off": site_off,
                "raw_word": raw_word,
            }
        )

    # Two-pass placement: measure assembled sizes, allocate final VAs, then assemble with real VAs.
    records: list[PatchRecord] = []
    placed_stubs: list[tuple[dict[str, Any], int, int]] = []
    for item in resolved:
        probe_blob = _build_trampoline(item["kind"], 0, sym, item["raw_word"], enable_logs)
        stub_off = pool.reserve(len(probe_blob), 4)
        stub_va = text_seg.virtual_address + (stub_off - text_seg.file_offset)
        placed_stubs.append((item, stub_off, stub_va))

    for item, stub_off, stub_va in placed_stubs:
        final_blob = _build_trampoline(item["kind"], stub_va, sym, item["raw_word"], enable_logs)
        raw_bytes[stub_off : stub_off + len(final_blob)] = final_blob
        bl_word = encode_arm64_bl(item["site_va"], stub_va)
        struct.pack_into("<I", raw_bytes, item["site_off"], bl_word)
        records.append(
            PatchRecord(
                name=item["name"],
                entry_va=item["entry_va"],
                site_va=item["site_va"],
                stub_va=stub_va,
                stub_size=len(final_blob),
            )
        )

    if apply_hdr_patches:
        for gate in profile.get("hdr_gates", []):
            va = int(gate["va"], 16)
            off = _va_to_file_offset(text_sec.virtual_address, text_sec.offset, text_sec.size, va)
            insn = _disasm_single(bytes(raw_bytes[off : off + 4]), va)
            if (insn.mnemonic, insn.op_str) != (gate["expect_mnemonic"], gate["expect_op_str"]):
                raise ValueError(
                    f"Gate '{gate['name']}' mismatch at {hex(va)}: "
                    f"expected '{gate['expect_mnemonic']} {gate['expect_op_str']}', "
                    f"got '{insn.mnemonic} {insn.op_str}'"
                )
            struct.pack_into("<I", raw_bytes, off, int(gate["replacement"], 16))
            records.append(PatchRecord(name=gate["name"], entry_va=va, site_va=va, stub_va=0, stub_size=0))

    return records
