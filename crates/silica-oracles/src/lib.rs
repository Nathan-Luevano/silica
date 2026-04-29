#![allow(missing_docs)]

pub mod capstone;
pub mod llvm;
pub mod llvm_spike;
pub mod spec;
pub mod unicorn;

pub trait Oracle: Send + Sync {
    fn name(&self) -> &'static str;
    fn decode(&self, word: u32) -> bool;
    fn disassemble(&self, word: u32) -> Option<String>;

    // Batched validity decoding for high-throughput bitmap population
    fn decode_batch_valid(&self, words: &[u32], bitmap: &mut [u8], bit_offset: usize) {
        for (i, &w) in words.iter().enumerate() {
            if self.decode_isolated(w) {
                let bit_idx = bit_offset + i;
                bitmap[bit_idx / 8] |= 1 << (bit_idx % 8);
            }
        }
    }

    // Batched disassembly decoding
    fn decode_batch_disasm(&self, words: &[u32], results: &mut [Option<String>]) {
        for (i, &w) in words.iter().enumerate() {
            results[i] = self.disassemble_isolated(w);
        }
    }

    // Panic-isolated single-instruction decode using std::panic::catch_unwind
    // NOTE: catch_unwind covers Rust-level panics, not native C-level segfaults (SIGSEGV)
    fn decode_isolated(&self, word: u32) -> bool {
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| self.decode(word)))
            .unwrap_or(false)
    }

    // Panic-isolated single-instruction disassemble using std::panic::catch_unwind
    // NOTE: catch_unwind covers Rust-level panics, not native C-level segfaults (SIGSEGV)
    fn disassemble_isolated(&self, word: u32) -> Option<String> {
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| self.disassemble(word)))
            .unwrap_or(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct PanickingOracle;
    impl Oracle for PanickingOracle {
        fn name(&self) -> &'static str {
            "panicking"
        }
        fn decode(&self, word: u32) -> bool {
            if word == 0xDEADBEEF {
                panic!("simulated disassembler crash");
            }
            word == 0xD503201F
        }
        fn disassemble(&self, word: u32) -> Option<String> {
            if word == 0xDEADBEEF {
                panic!("simulated disassembler crash");
            }
            if word == 0xD503201F {
                Some("nop".to_string())
            } else {
                None
            }
        }
    }

    #[test]
    fn test_batch_and_crash_isolation() {
        let oracle = PanickingOracle;
        let words = [0xD503201F, 0xDEADBEEF, 0x00010000];

        // test crash isolation on single decode
        assert!(oracle.decode_isolated(0xD503201F));
        assert!(!oracle.decode_isolated(0xDEADBEEF));
        assert_eq!(
            oracle.disassemble_isolated(0xD503201F),
            Some("nop".to_string())
        );
        assert_eq!(oracle.disassemble_isolated(0xDEADBEEF), None);

        // test batched bitmap decoding
        let mut bitmap = [0u8; 1];
        oracle.decode_batch_valid(&words, &mut bitmap, 0);
        // bit 0 is set (0xD503201F valid), bits 1 and 2 are clear
        assert_eq!(bitmap[0], 0b00000001);

        // test batched disassembly decoding
        let mut results = vec![None; 3];
        oracle.decode_batch_disasm(&words, &mut results);
        assert_eq!(results[0], Some("nop".to_string()));
        assert_eq!(results[1], None);
        assert_eq!(results[2], None);
    }
}
