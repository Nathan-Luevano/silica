use std::ffi::c_void;
use std::sync::Mutex;

use crate::Oracle;

type UcEngine = *mut c_void;
type UcHook = usize;

const UC_ARCH_ARM64: u32 = 2;
const UC_MODE_ARM: u32 = 0;
const UC_PROT_ALL: u32 = 7;
const UC_ERR_OK: u32 = 0;

const UC_HOOK_INTR: i32 = 1 << 0;
const UC_HOOK_MEM_READ_UNMAPPED: i32 = 1 << 4;
const UC_HOOK_MEM_WRITE_UNMAPPED: i32 = 1 << 5;
const UC_HOOK_MEM_FETCH_UNMAPPED: i32 = 1 << 6;
const UC_HOOK_MEM_UNMAPPED: i32 =
    UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED | UC_HOOK_MEM_FETCH_UNMAPPED;
const UC_HOOK_INSN_INVALID: i32 = 1 << 14;

// ARM64 QEMU exception numbers delivered through UC_HOOK_INTR
const EXCP_UDEF: u32 = 1;

const CODE_ADDR: u64 = 0x10000;
const CODE_SIZE: usize = 0x10000;
const DATA_ADDR: u64 = 0x100000;
const DATA_SIZE: usize = 0x100000;
const SAFE_PTR: u64 = 0x180000;
const RETURN_ADDR: u64 = CODE_ADDR + 4;

const UC_ARM64_REG_X29: i32 = 43;
const UC_ARM64_REG_X30: i32 = 44;
const UC_ARM64_REG_SP: i32 = 46;
const UC_ARM64_REG_X0: i32 = 241;

extern "C" {
    fn uc_open(arch: u32, mode: u32, uc: *mut UcEngine) -> u32;
    fn uc_close(uc: UcEngine) -> u32;
    fn uc_mem_map(uc: UcEngine, address: u64, size: usize, perms: u32) -> u32;
    fn uc_mem_write(uc: UcEngine, address: u64, bytes: *const u8, size: usize) -> u32;
    fn uc_reg_write(uc: UcEngine, regid: i32, value: *const c_void) -> u32;
    fn uc_emu_start(uc: UcEngine, begin: u64, until: u64, timeout: u64, count: usize) -> u32;
    fn uc_emu_stop(uc: UcEngine) -> u32;
    fn uc_hook_add(
        uc: UcEngine,
        hh: *mut UcHook,
        hook_type: i32,
        callback: *const c_void,
        user_data: *mut c_void,
        begin: u64,
        end: u64,
        ...
    ) -> u32;
    fn uc_hook_del(uc: UcEngine, hh: UcHook) -> u32;
}

#[derive(Default)]
struct ExecutionTrace {
    invalid_hook_fired: bool,
    undef_exception_fired: bool,
    valid_intr_fired: bool,
    mem_hook_fired: bool,
}

extern "C" fn hook_invalid_cb(uc: UcEngine, user_data: *mut c_void) -> bool {
    let trace = unsafe { &mut *(user_data as *mut ExecutionTrace) };
    trace.invalid_hook_fired = true;
    unsafe {
        uc_emu_stop(uc);
    }
    false
}

extern "C" fn hook_mem_unmapped_cb(
    uc: UcEngine,
    _mem_type: i32,
    _address: u64,
    _size: i32,
    _value: i64,
    user_data: *mut c_void,
) -> bool {
    let trace = unsafe { &mut *(user_data as *mut ExecutionTrace) };
    trace.mem_hook_fired = true;
    unsafe {
        uc_emu_stop(uc);
    }
    true
}

extern "C" fn hook_intr_cb(uc: UcEngine, intno: u32, user_data: *mut c_void) {
    let trace = unsafe { &mut *(user_data as *mut ExecutionTrace) };
    if intno == EXCP_UDEF {
        // EXCP_UDEF (1) is QEMU's undefined instruction exception for unallocated encodings
        trace.undef_exception_fired = true;
    } else {
        // EXCP_SWI (2), EXCP_BKPT (7), etc. are valid system/interrupt instructions
        trace.valid_intr_fired = true;
    }
    unsafe {
        uc_emu_stop(uc);
    }
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
            return Err(format!(
                "uc_mem_map code failed with error code {}",
                map_err
            ));
        }

        let map_data_err = unsafe { uc_mem_map(engine, DATA_ADDR, DATA_SIZE, UC_PROT_ALL) };
        if map_data_err != UC_ERR_OK {
            unsafe {
                uc_close(engine);
            }
            return Err(format!(
                "uc_mem_map data failed with error code {}",
                map_data_err
            ));
        }

        // write NOPs throughout code region after CODE_ADDR
        let nop_bytes = 0xD503201Fu32.to_le_bytes();
        for offset in (4..128).step_by(4) {
            unsafe {
                uc_mem_write(engine, CODE_ADDR + offset, nop_bytes.as_ptr(), 4);
            }
        }

        Ok(Self {
            engine: Mutex::new(engine),
        })
    }

    fn init_registers(engine: UcEngine) {
        let safe_ptr = SAFE_PTR;
        let return_addr = RETURN_ADDR;

        for reg_id in 0..29 {
            unsafe {
                uc_reg_write(
                    engine,
                    UC_ARM64_REG_X0 + reg_id,
                    &safe_ptr as *const _ as *const c_void,
                );
            }
        }
        unsafe {
            uc_reg_write(
                engine,
                UC_ARM64_REG_X29,
                &safe_ptr as *const _ as *const c_void,
            );
            uc_reg_write(
                engine,
                UC_ARM64_REG_SP,
                &safe_ptr as *const _ as *const c_void,
            );
            uc_reg_write(
                engine,
                UC_ARM64_REG_X30,
                &return_addr as *const _ as *const c_void,
            );
        }
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
        let engine = *guard;

        Self::init_registers(engine);

        let mut trace = ExecutionTrace::default();
        let trace_ptr = &mut trace as *mut ExecutionTrace as *mut c_void;

        let mut h_invalid: UcHook = 0;
        let mut h_mem: UcHook = 0;
        let mut h_intr: UcHook = 0;

        unsafe {
            uc_hook_add(
                engine,
                &mut h_invalid,
                UC_HOOK_INSN_INVALID,
                hook_invalid_cb as *const c_void,
                trace_ptr,
                1,
                0,
            );
            uc_hook_add(
                engine,
                &mut h_mem,
                UC_HOOK_MEM_UNMAPPED,
                hook_mem_unmapped_cb as *const c_void,
                trace_ptr,
                1,
                0,
            );
            uc_hook_add(
                engine,
                &mut h_intr,
                UC_HOOK_INTR,
                hook_intr_cb as *const c_void,
                trace_ptr,
                1,
                0,
            );
        }

        let bytes = word.to_le_bytes();
        let write_err = unsafe { uc_mem_write(engine, CODE_ADDR, bytes.as_ptr(), bytes.len()) };
        if write_err != UC_ERR_OK {
            unsafe {
                uc_hook_del(engine, h_invalid);
                uc_hook_del(engine, h_mem);
                uc_hook_del(engine, h_intr);
            }
            return false;
        }

        // execute exactly 1 instruction step. timeout is microseconds, not
        // 0 (unlimited) -- WFE/WFI-class encodings block waiting for an
        // event/interrupt that never comes with no timeout set, found by
        // running the real sweep: two words hung forever with no way out.
        // this is defense-in-depth; the sweep's own process-level timeout
        // (crates/silica-sweep/src/isolate.rs) is what actually recovers.
        const EMU_TIMEOUT_US: u64 = 2_000_000;
        let emu_err = unsafe { uc_emu_start(engine, CODE_ADDR, CODE_ADDR + 4, EMU_TIMEOUT_US, 1) };

        unsafe {
            uc_hook_del(engine, h_invalid);
            uc_hook_del(engine, h_mem);
            uc_hook_del(engine, h_intr);
        }

        if trace.invalid_hook_fired || trace.undef_exception_fired {
            return false;
        }

        emu_err == UC_ERR_OK || trace.valid_intr_fired || trace.mem_hook_fired
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
    fn test_unicorn_oracle_instruction_classes() {
        let oracle = UnicornOracle::new().expect("failed to initialize UnicornOracle");
        assert_eq!(oracle.name(), "unicorn");

        // NOP: 0xD503201F
        let nop_word = 0xD503201F;
        assert!(oracle.decode(nop_word), "NOP must be valid");

        // RET: 0xD65F03C0 (branches to X30)
        let ret_word = 0xD65F03C0;
        assert!(oracle.decode(ret_word), "RET must be valid");

        // SVC #0: 0xD4000001 (system supervisor call interrupt)
        let svc_word = 0xD4000001;
        assert!(oracle.decode(svc_word), "SVC must be valid");

        // BRK #0: 0xD4200000 (debug breakpoint interrupt)
        let brk_word = 0xD4200000;
        assert!(oracle.decode(brk_word), "BRK must be valid");

        // ADD x0, x1, x2: 0x8B020020 (data processing register)
        let add_word = 0x8B020020;
        assert!(oracle.decode(add_word), "ADD must be valid");

        // LDR x0, [x1]: 0xF9400020 (load from memory)
        let ldr_word = 0xF9400020;
        assert!(oracle.decode(ldr_word), "LDR must be valid");

        // STR x0, [x1]: 0xF9000020 (store to memory)
        let str_word = 0xF9000020;
        assert!(oracle.decode(str_word), "STR must be valid");

        // B #16: 0x14000004 (direct branch)
        let b_word = 0x14000004;
        assert!(oracle.decode(b_word), "Branch must be valid");

        // 0x00010000 is genuinely unallocated in ARMv8/v9
        assert!(!oracle.decode(0x00010000), "0x00010000 must be invalid");
    }

    #[test]
    fn test_unicorn_vs_spec_random_sample() {
        use crate::spec::SpecOracle;
        use std::path::Path;

        let artifact_path = Path::new("artifacts/decode-table.bin");
        let alt_path = Path::new("../../artifacts/decode-table.bin");
        let path_to_use = if artifact_path.exists() {
            artifact_path
        } else if alt_path.exists() {
            alt_path
        } else {
            return;
        };

        let spec_oracle = SpecOracle::from_file(path_to_use).expect("failed to load SpecOracle");
        let unicorn_oracle = UnicornOracle::new().expect("failed to initialize UnicornOracle");

        let mut rng_state: u64 = 0x123456789ABCDEF0;
        let mut lcg = || -> u32 {
            rng_state = rng_state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            (rng_state >> 32) as u32
        };

        let sample_count = 20_000;
        let mut both_valid = 0;
        let mut both_invalid = 0;
        let mut false_positives = 0; // spec unallocated, unicorn valid
        let mut false_negatives = 0; // spec allocated, unicorn invalid

        let mut false_neg_samples: Vec<(u32, String)> = Vec::new();

        for _ in 0..sample_count {
            let word = lcg();
            let spec_valid = spec_oracle.decode(word);
            let unicorn_valid = unicorn_oracle.decode(word);

            match (spec_valid, unicorn_valid) {
                (true, true) => both_valid += 1,
                (false, false) => both_invalid += 1,
                (false, true) => false_positives += 1,
                (true, false) => {
                    false_negatives += 1;
                    if false_neg_samples.len() < 15 {
                        let (_, maybe_form, _) = spec_oracle.classify(word);
                        let form_info = maybe_form
                            .map(|f| f.psname.clone())
                            .unwrap_or_else(|| "unknown".to_string());
                        false_neg_samples.push((word, form_info));
                    }
                }
            }
        }

        let total_agreement = both_valid + both_invalid;
        let agreement_pct = (total_agreement as f64 / sample_count as f64) * 100.0;
        let fn_pct = (false_negatives as f64 / sample_count as f64) * 100.0;
        let fp_pct = (false_positives as f64 / sample_count as f64) * 100.0;

        println!("=== UNICORN vs SPEC 20,000-WORD RANDOM SAMPLE ===");
        println!("Total samples: {}", sample_count);
        println!(
            "Raw Agreement: {}/{} ({:.2}%)",
            total_agreement, sample_count, agreement_pct
        );
        println!("Both Valid (True Positive): {}", both_valid);
        println!("Both Invalid (True Negative): {}", both_invalid);
        println!(
            "False Positives (spec unallocated, unicorn valid): {} ({:.2}%)",
            false_positives, fp_pct
        );
        println!(
            "False Negatives (spec allocated, unicorn invalid): {} ({:.2}%)",
            false_negatives, fn_pct
        );
        println!("Sample False Negatives (word, spec psname):");
        for (w, psname) in &false_neg_samples {
            println!("  0x{:08X}: {}", w, psname);
        }

        // 0% false positives: Unicorn must never call something valid if the spec says unallocated
        assert_eq!(
            false_positives, 0,
            "UnicornOracle had false positives against SpecOracle"
        );
    }
}

#[test]
fn test_all_oracles_cross_check() {
    use crate::capstone::CapstoneOracle;
    use crate::llvm::LlvmOracle;
    use crate::spec::SpecOracle;
    use std::path::Path;

    let artifact_path = Path::new("artifacts/decode-table.bin");
    let alt_path = Path::new("../../artifacts/decode-table.bin");
    let path_to_use = if artifact_path.exists() {
        artifact_path
    } else if alt_path.exists() {
        alt_path
    } else {
        return;
    };

    let spec = SpecOracle::from_file(path_to_use).expect("load spec");
    let unicorn = UnicornOracle::new().expect("load unicorn");
    let llvm = LlvmOracle::new().expect("load llvm");
    let capstone = CapstoneOracle::new().expect("load capstone");

    let mut rng: u64 = 0x123456789ABCDEF0;
    let mut lcg = || -> u32 {
        rng = rng
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (rng >> 32) as u32
    };

    let n = 20_000;
    let mut spec_count = 0;
    let mut unicorn_count = 0;
    let mut llvm_count = 0;
    let mut capstone_count = 0;

    let mut spec_and_llvm_valid = 0;
    let mut spec_and_capstone_valid = 0;
    let mut unicorn_and_llvm_valid = 0;

    for _ in 0..n {
        let w = lcg();
        let s = spec.decode(w);
        let u = unicorn.decode(w);
        let l = llvm.decode(w);
        let c = capstone.decode(w);

        if s {
            spec_count += 1;
        }
        if u {
            unicorn_count += 1;
        }
        if l {
            llvm_count += 1;
        }
        if c {
            capstone_count += 1;
        }

        if s && l {
            spec_and_llvm_valid += 1;
        }
        if s && c {
            spec_and_capstone_valid += 1;
        }
        if u && l {
            unicorn_and_llvm_valid += 1;
        }
    }

    println!("=== 4-ORACLE 20,000-WORD RANDOM COMPARISON ===");
    println!(
        "Spec allocated:     {}/{} ({:.2}%)",
        spec_count,
        n,
        spec_count as f64 / n as f64 * 100.0
    );
    println!(
        "Unicorn valid:      {}/{} ({:.2}%)",
        unicorn_count,
        n,
        unicorn_count as f64 / n as f64 * 100.0
    );
    println!(
        "LLVM valid:         {}/{} ({:.2}%)",
        llvm_count,
        n,
        llvm_count as f64 / n as f64 * 100.0
    );
    println!(
        "Capstone valid:     {}/{} ({:.2}%)",
        capstone_count,
        n,
        capstone_count as f64 / n as f64 * 100.0
    );
    println!(
        "Spec & LLVM both:   {}/{} ({:.2}%)",
        spec_and_llvm_valid,
        n,
        spec_and_llvm_valid as f64 / n as f64 * 100.0
    );
    println!(
        "Spec & Capstone:    {}/{} ({:.2}%)",
        spec_and_capstone_valid,
        n,
        spec_and_capstone_valid as f64 / n as f64 * 100.0
    );
    println!(
        "Unicorn & LLVM:     {}/{} ({:.2}%)",
        unicorn_and_llvm_valid,
        n,
        unicorn_and_llvm_valid as f64 / n as f64 * 100.0
    );
}
