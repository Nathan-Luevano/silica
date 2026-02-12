fn main() {
    let text =
        silica_oracles::llvm_spike::disasm_one(0xD65F03C0).expect("LLVM failed to disassemble RET");
    println!("0xD65F03C0 -> {text}");
    assert!(
        text.contains("ret"),
        "expected 'ret' in disassembly, got: {text}"
    );
    println!("LLVM C API spike: OK");
}
