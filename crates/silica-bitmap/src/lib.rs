#![allow(missing_docs)]

use std::fs::OpenOptions;
use std::io;
use std::path::Path;

use memmap2::{MmapMut, MmapOptions};

pub const ENCODING_SPACE: u64 = 1u64 << 32;

// bit index == encoding value, LSB-first within each byte (docs/formats.md)
pub struct Bitmap {
    mmap: MmapMut,
    bits: u64,
}

fn bytes_for_bits(bits: u64) -> u64 {
    bits.div_ceil(8)
}

impl Bitmap {
    // creates a zero-filled file of the right size if absent, opens (and
    // size-checks) it otherwise. shared across concurrent shard writers
    // since each shard only ever touches its own disjoint byte range.
    pub fn open_or_create<P: AsRef<Path>>(path: P, bits: u64) -> io::Result<Self> {
        let want_bytes = bytes_for_bits(bits);
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;
        let cur_len = file.metadata()?.len();
        if cur_len != want_bytes {
            file.set_len(want_bytes)?;
        }
        let mmap = unsafe { MmapOptions::new().len(want_bytes as usize).map_mut(&file)? };
        Ok(Bitmap { mmap, bits })
    }

    pub fn len_bits(&self) -> u64 {
        self.bits
    }

    pub fn set_bit(&mut self, index: u64) {
        debug_assert!(index < self.bits);
        let byte = (index / 8) as usize;
        let bit = (index % 8) as u8;
        self.mmap[byte] |= 1 << bit;
    }

    pub fn get_bit(&self, index: u64) -> bool {
        debug_assert!(index < self.bits);
        let byte = (index / 8) as usize;
        let bit = (index % 8) as u8;
        (self.mmap[byte] >> bit) & 1 == 1
    }

    pub fn flush(&self) -> io::Result<()> {
        self.mmap.flush()
    }

    // raw byte access into a bit range, e.g. for hashing/popcounting one
    // shard's slice without touching the rest of a 512MiB file
    pub fn byte_range(&self, start_bit: u64, end_bit: u64) -> &[u8] {
        assert_eq!(start_bit % 8, 0);
        assert_eq!(end_bit % 8, 0);
        &self.mmap[(start_bit / 8) as usize..(end_bit / 8) as usize]
    }

    pub fn byte_range_mut(&mut self, start_bit: u64, end_bit: u64) -> &mut [u8] {
        assert_eq!(start_bit % 8, 0);
        assert_eq!(end_bit % 8, 0);
        &mut self.mmap[(start_bit / 8) as usize..(end_bit / 8) as usize]
    }

    pub fn popcount(&self) -> u64 {
        popcount_bytes(&self.mmap)
    }

    pub fn popcount_range(&self, start_bit: u64, end_bit: u64) -> u64 {
        popcount_bytes(self.byte_range(start_bit, end_bit))
    }
}

pub fn popcount_bytes(data: &[u8]) -> u64 {
    data.iter().map(|b| b.count_ones() as u64).sum()
}

// xor two equal-length byte slices, used for cross-oracle validity diffing
// (not wired into the sweep CLI yet, but the primitive needs to exist per
// design.md §6 — "cross-oracle validity disagreement is a single XOR")
pub fn xor_bytes(a: &[u8], b: &[u8]) -> Vec<u8> {
    assert_eq!(a.len(), b.len());
    a.iter().zip(b.iter()).map(|(x, y)| x ^ y).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn create_zero_filled_and_set_get() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("test.bin");
        let mut bm = Bitmap::open_or_create(&path, 1 << 16).unwrap();
        assert_eq!(bm.popcount(), 0);
        assert!(!bm.get_bit(42));
        bm.set_bit(42);
        assert!(bm.get_bit(42));
        assert_eq!(bm.popcount(), 1);
        assert_eq!(std::fs::metadata(&path).unwrap().len(), (1u64 << 16) / 8);
    }

    #[test]
    fn reopen_preserves_contents() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("test.bin");
        {
            let mut bm = Bitmap::open_or_create(&path, 1 << 16).unwrap();
            bm.set_bit(7);
            bm.flush().unwrap();
        }
        let bm2 = Bitmap::open_or_create(&path, 1 << 16).unwrap();
        assert!(bm2.get_bit(7));
        assert_eq!(bm2.popcount(), 1);
    }

    #[test]
    fn popcount_range_matches_full_range() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("test.bin");
        let mut bm = Bitmap::open_or_create(&path, 1 << 20).unwrap();
        for i in [0u64, 1, 8, 100, 1000, (1 << 20) - 1] {
            bm.set_bit(i);
        }
        assert_eq!(bm.popcount(), 6);
        assert_eq!(bm.popcount_range(0, 1 << 20), 6);
        assert_eq!(bm.popcount_range(0, 8), 2); // bits 0,1 in byte 0
    }

    #[test]
    fn xor_finds_disagreement() {
        let a = [0b0000_0011u8];
        let b = [0b0000_0001u8];
        let x = xor_bytes(&a, &b);
        assert_eq!(x, [0b0000_0010]);
    }
}
