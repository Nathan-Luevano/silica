use std::ffi::CStr;
use std::os::raw::c_char;
use std::sync::Mutex;

use crate::Oracle;

type Csh = usize;

#[repr(C)]
#[derive(Copy, Clone)]
struct CsInsn {
    id: u32,
    address: u64,
    size: u16,
    bytes: [u8; 24],
    mnemonic: [c_char; 32],
    op_str: [c_char; 160],
    detail: *mut std::ffi::c_void,
}

const CS_ARCH_ARM64: u32 = 1;
const CS_MODE_ARM: u32 = 0;
const CS_ERR_OK: u32 = 0;

extern "C" {
    fn cs_open(arch: u32, mode: u32, handle: *mut Csh) -> u32;
    fn cs_close(handle: *mut Csh) -> u32;
    fn cs_disasm(
        handle: Csh,
        code: *const u8,
        code_size: usize,
        address: u64,
        count: usize,
        insn: *mut *mut CsInsn,
    ) -> usize;
    fn cs_free(insn: *mut CsInsn, count: usize);
}

pub struct CapstoneOracle {
    handle: Mutex<Csh>,
}

unsafe impl Send for CapstoneOracle {}
unsafe impl Sync for CapstoneOracle {}

impl CapstoneOracle {
    pub fn new() -> Result<Self, String> {
        let mut handle: Csh = 0;
        let err = unsafe { cs_open(CS_ARCH_ARM64, CS_MODE_ARM, &mut handle) };
        if err != CS_ERR_OK {
            return Err(format!("cs_open failed with error code {}", err));
        }
        Ok(Self {
            handle: Mutex::new(handle),
        })
    }
}

impl Drop for CapstoneOracle {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.handle.lock() {
            if *guard != 0 {
                unsafe {
                    cs_close(&mut *guard);
                }
                *guard = 0;
            }
        }
    }
}

impl Oracle for CapstoneOracle {
    fn name(&self) -> &'static str {
        "capstone"
    }

    fn decode(&self, word: u32) -> bool {
        self.disassemble(word).is_some()
    }

    fn disassemble(&self, word: u32) -> Option<String> {
        let guard = self.handle.lock().ok()?;
        if *guard == 0 {
            return None;
        }

        let bytes = word.to_le_bytes();
        let mut insn_ptr: *mut CsInsn = std::ptr::null_mut();

        let count = unsafe { cs_disasm(*guard, bytes.as_ptr(), bytes.len(), 0, 1, &mut insn_ptr) };

        if count == 0 || insn_ptr.is_null() {
            return None;
        }

        let insn = unsafe { &*insn_ptr };
        let mnemonic = unsafe { CStr::from_ptr(insn.mnemonic.as_ptr()) }.to_string_lossy();
        let op_str = unsafe { CStr::from_ptr(insn.op_str.as_ptr()) }.to_string_lossy();

        let text = if op_str.trim().is_empty() {
            mnemonic.trim().to_string()
        } else {
            format!("{} {}", mnemonic.trim(), op_str.trim())
        };

        unsafe {
            cs_free(insn_ptr, count);
        }

        Some(text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_capstone_oracle() {
        let oracle = CapstoneOracle::new().expect("failed to initialize CapstoneOracle");
        assert_eq!(oracle.name(), "capstone");

        // RET: 0xD65F03C0 -> "ret"
        let ret_word = 0xD65F03C0;
        assert!(oracle.decode(ret_word));
        assert_eq!(oracle.disassemble(ret_word), Some("ret".to_string()));

        // NOP: 0xD503201F -> "nop"
        let nop_word = 0xD503201F;
        assert!(oracle.decode(nop_word));
        assert_eq!(oracle.disassemble(nop_word), Some("nop".to_string()));

        // 0x00010000 is invalid in Capstone
        assert!(!oracle.decode(0x00010000));
        assert_eq!(oracle.disassemble(0x00010000), None);
    }
}
