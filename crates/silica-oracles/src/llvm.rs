use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::ptr;
use std::sync::{Mutex, Once};

use crate::Oracle;

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

    fn LLVMDisasmDispose(dc: LLVMDisasmContextRef);

    fn LLVMInitializeAArch64TargetInfo();
    fn LLVMInitializeAArch64Target();
    fn LLVMInitializeAArch64TargetMC();
    fn LLVMInitializeAArch64Disassembler();
}

static INIT: Once = Once::new();

fn init_targets() {
    INIT.call_once(|| unsafe {
        LLVMInitializeAArch64TargetInfo();
        LLVMInitializeAArch64Target();
        LLVMInitializeAArch64TargetMC();
        LLVMInitializeAArch64Disassembler();
    });
}

pub struct LlvmOracle {
    dc: Mutex<LLVMDisasmContextRef>,
}

// llvm-c disassembler context can be moved across threads when protected by a mutex
unsafe impl Send for LlvmOracle {}
unsafe impl Sync for LlvmOracle {}

impl LlvmOracle {
    pub fn new() -> Result<Self, String> {
        init_targets();
        let triple = CString::new("aarch64-unknown-linux-gnu").unwrap();
        let dc = unsafe {
            LLVMCreateDisasm(
                triple.as_ptr(),
                ptr::null_mut(),
                0,
                ptr::null(),
                ptr::null(),
            )
        };
        if dc.is_null() {
            return Err("LLVMCreateDisasm returned null context".to_string());
        }
        Ok(Self { dc: Mutex::new(dc) })
    }
}

impl Drop for LlvmOracle {
    fn drop(&mut self) {
        if let Ok(guard) = self.dc.lock() {
            if !guard.is_null() {
                unsafe {
                    LLVMDisasmDispose(*guard);
                }
            }
        }
    }
}

impl Oracle for LlvmOracle {
    fn name(&self) -> &'static str {
        "llvm"
    }

    fn decode(&self, word: u32) -> bool {
        self.disassemble(word).is_some()
    }

    fn disassemble(&self, word: u32) -> Option<String> {
        let guard = self.dc.lock().ok()?;
        if guard.is_null() {
            return None;
        }
        let mut bytes = word.to_le_bytes();
        let mut out = [0u8; 128];

        let consumed = unsafe {
            LLVMDisasmInstruction(
                *guard,
                bytes.as_mut_ptr(),
                bytes.len() as u64,
                0,
                out.as_mut_ptr() as *mut c_char,
                out.len(),
            )
        };

        // llvm returns 0 for invalid encodings or when decode fails
        if consumed == 0 {
            return None;
        }

        let s = unsafe { CStr::from_ptr(out.as_ptr() as *const c_char) };
        let text = s.to_string_lossy().trim().to_string();
        if text.is_empty() {
            None
        } else {
            Some(text)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_llvm_oracle() {
        let oracle = LlvmOracle::new().expect("failed to initialize LlvmOracle");
        assert_eq!(oracle.name(), "llvm");

        // RET: 0xD65F03C0 -> "ret"
        let ret_word = 0xD65F03C0;
        assert!(oracle.decode(ret_word));
        assert_eq!(oracle.disassemble(ret_word), Some("ret".to_string()));

        // NOP: 0xD503201F -> "nop"
        let nop_word = 0xD503201F;
        assert!(oracle.decode(nop_word));
        assert_eq!(oracle.disassemble(nop_word), Some("nop".to_string()));

        // 0x00010000 is invalid
        assert!(!oracle.decode(0x00010000));
        assert_eq!(oracle.disassemble(0x00010000), None);
    }
}
