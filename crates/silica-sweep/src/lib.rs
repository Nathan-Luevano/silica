#![allow(missing_docs)]

pub mod g4;
pub mod g4_disasm;
pub mod isolate;
pub mod oracle;
pub mod sweep;

use serde::{Deserialize, Serialize};

pub const SHARD_BITS: u64 = 1 << 24;
pub const N_SHARDS: u32 = 256;
pub const TOTAL_BITS: u64 = 1u64 << 32;
pub const ORACLES: [&str; 4] = ["capstone", "llvm", "spec", "unicorn"];

// default batch size for a single worker subprocess invocation. small
// enough that a crash doesn't lose much throughput, large enough that
// process-spawn overhead doesn't dominate. bisected down further on crash.
pub const DEFAULT_BATCH_BITS: u64 = 1 << 22;

pub fn shard_range(shard: u32) -> (u64, u64) {
    let start = shard as u64 * SHARD_BITS;
    (start, start + SHARD_BITS)
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ShardRecord {
    pub shard_id: u32,
    pub start: u64,
    pub end: u64,
    pub oracles: Vec<String>,
    pub valid_counts: std::collections::BTreeMap<String, u64>,
    pub crash_count: u64,
    pub untriaged_crash_count: u64,
    pub content_hash: String,
    pub duration_ms: u64,
    pub status: String,
}
