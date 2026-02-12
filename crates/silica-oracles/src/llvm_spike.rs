use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::ptr;

#[allow(non_camel_case_types)]
type LLVMDisasmContextRef = *mut c_void;

extern "C" {
    fn LLVMCreateDisasm(
        triple_name: *const c_char,
        disinfo: *mut c_void,
        tag_type: c_int,
        get_op_info: *const c_void,
        symbol_lookup: *const c_void,
    ) -> LLVMDisasmContextRef;

    fn LLVMDisasmInstruction(
        dc: LLVMDisasmContextRef,
        bytes: *mut u8,
        bytes_size: u64,
        pc: u64,
        out_string: *mut c_char,
        out_string_size: usize,
    ) -> usize;

    fn LLVMInitializeAArch64TargetInfo();
    fn LLVMInitializeAArch64Target();
    fn LLVMInitializeAArch64TargetMC();
    fn LLVMInitializeAArch64Disassembler();
}

// proves the llvm-c disassembler links and works in-process, before the
// sweep engine is built around it (design.md §3.1).
pub fn disasm_one(word: u32) -> Option<String> {
    unsafe {
        LLVMInitializeAArch64TargetInfo();
        LLVMInitializeAArch64Target();
        LLVMInitializeAArch64TargetMC();
        LLVMInitializeAArch64Disassembler();

        let triple = CString::new("aarch64-unknown-linux-gnu").unwrap();
        let dc = LLVMCreateDisasm(
            triple.as_ptr(),
            ptr::null_mut(),
            0,
            ptr::null(),
            ptr::null(),
        );
        if dc.is_null() {
            return None;
        }

        let mut bytes = word.to_le_bytes();
        let mut out = [0u8; 128];

        let consumed = LLVMDisasmInstruction(
            dc,
            bytes.as_mut_ptr(),
            bytes.len() as u64,
            0,
            out.as_mut_ptr() as *mut c_char,
            out.len(),
        );
        if consumed == 0 {
            return None;
        }

        Some(
            CStr::from_ptr(out.as_ptr() as *const c_char)
                .to_string_lossy()
                .trim()
                .to_string(),
        )
    }
}
