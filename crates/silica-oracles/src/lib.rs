#![allow(missing_docs)]

pub mod llvm_spike;

pub trait Oracle {
    fn name(&self) -> &'static str;
    fn decode(&self, word: u32) -> bool;
}
