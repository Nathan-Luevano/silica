use std::ffi::c_void;
use std::sync::Mutex;

use crate::Oracle;

type UcEngine = *mut c_void;

const UC_ARCH_ARM64: u32 = 2;
const UC_MODE_ARM: u32 = 0;
const UC_PROT_ALL: u32 = 7;
const UC_ERR_OK: u32 = 0;

const CODE_ADDR: u64 = 0x10000;
const CODE_SIZE: usize = 0x1000;

extern "C" {
    fn uc_open(arch: u32, mode: u32, uc: *mut UcEngine) -> u32;
    fn uc_close(uc: UcEngine) -> u32;
    fn uc_mem_map(uc: UcEngine, address: u64, size: usize, perms: u32) -> u32;
    fn uc_mem_write(uc: UcEngine, address: u64, bytes: *const u8, size: usize) -> u32;
    fn uc_emu_start(uc: UcEngine, begin: u64, until: u64, timeout: u64, count: usize) -> u32;
}

pub struct UnicornOracle {
    engine: Mutex<UcEngine>,
}

unsafe impl Send for UnicornOracle {}
unsafe impl Sync for UnicornOracle {}

impl UnicornOracle {
    pub fn new() -> Result<Self, String> {
        let mut engine: UcEngine = std::ptr::null_mut();
        let err = unsafe { uc_open(UC_ARCH_ARM64, UC_MODE_ARM, &mut engine) };
        if err != UC_ERR_OK || engine.is_null() {
            return Err(format!("uc_open failed with error code {}", err));
        }

        let map_err = unsafe { uc_mem_map(engine, CODE_ADDR, CODE_SIZE, UC_PROT_ALL) };
        if map_err != UC_ERR_OK {
            unsafe {
                uc_close(engine);
            }
            return Err(format!("uc_mem_map failed with error code {}", map_err));
        }

        Ok(Self {
            engine: Mutex::new(engine),
        })
    }
}

impl Drop for UnicornOracle {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.engine.lock() {
            if !guard.is_null() {
                unsafe {
                    uc_close(*guard);
                }
                *guard = std::ptr::null_mut();
            }
        }
    }
}

impl Oracle for UnicornOracle {
    fn name(&self) -> &'static str {
        "unicorn"
    }

    fn decode(&self, word: u32) -> bool {
        let guard = match self.engine.lock() {
            Ok(g) => g,
            Err(_) => return false,
        };
        if guard.is_null() {
            return false;
        }

        let bytes = word.to_le_bytes();
        let write_err = unsafe { uc_mem_write(*guard, CODE_ADDR, bytes.as_ptr(), bytes.len()) };
        if write_err != UC_ERR_OK {
            return false;
        }

        // execute exactly 1 instruction
        let emu_err = unsafe { uc_emu_start(*guard, CODE_ADDR, CODE_ADDR + 4, 0, 1) };
        emu_err == UC_ERR_OK
    }

    fn disassemble(&self, word: u32) -> Option<String> {
        if self.decode(word) {
            Some("<valid>".to_string())
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_unicorn_oracle() {
        let oracle = UnicornOracle::new().expect("failed to initialize UnicornOracle");
        assert_eq!(oracle.name(), "unicorn");

        // NOP: 0xD503201F executes cleanly in Unicorn
        let nop_word = 0xD503201F;
        assert!(oracle.decode(nop_word));
        assert_eq!(oracle.disassemble(nop_word), Some("<valid>".to_string()));

        // 0x00010000 is invalid
        assert!(!oracle.decode(0x00010000));
        assert_eq!(oracle.disassemble(0x00010000), None);
    }
}
