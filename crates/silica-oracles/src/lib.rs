#![allow(missing_docs)]

pub mod llvm_spike;
pub mod spec;

pub trait Oracle: Send + Sync {
    fn name(&self) -> &'static str;
    fn decode(&self, word: u32) -> bool;
    fn disassemble(&self, word: u32) -> Option<String>;
}
