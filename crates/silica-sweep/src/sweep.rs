use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

use sha2::{Digest, Sha256};
use silica_bitmap::Bitmap;

use crate::isolate::run_batch_isolated;
use crate::oracle::build_oracle;
use crate::verify_batch::{execute_jobs, merge_batch_bits, verification_jobs, BatchJob};
use crate::{shard_range, ShardRecord, DEFAULT_BATCH_BITS, ORACLES, TOTAL_BITS};

pub fn worker_main(
    oracle_name: &str,
    start: u64,
    end: u64,
    decode_table: &Path,
    result_file: &Path,
) -> Result<(), String> {
    // TEST ONLY: deliberately segfault when this exact word is handed to a
    // worker, to prove the parent's process-level isolation survives a
    // real SIGSEGV (catch_unwind does not -- see silica-oracles P2 notes).
    // Never set in normal operation.
    if let Ok(hex) = std::env::var("SILICA_SWEEP_TEST_CRASH_WORD") {
        if let Ok(cw) = u32::from_str_radix(hex.trim_start_matches("0x"), 16) {
            if (cw as u64) >= start && (cw as u64) < end {
                let p: *const u8 = std::ptr::null();
                unsafe {
                    std::ptr::read_volatile(p);
                }
            }
        }
    }

    // TEST ONLY: deliberately hang (never return) when this exact word is
    // handed to a worker, to prove isolate.rs's process-level timeout
    // recovers a genuinely stuck child. Never set in normal operation.
    if let Ok(hex) = std::env::var("SILICA_SWEEP_TEST_HANG_WORD") {
        if let Ok(hw) = u32::from_str_radix(hex.trim_start_matches("0x"), 16) {
            if (hw as u64) >= start && (hw as u64) < end {
                loop {
                    std::thread::sleep(std::time::Duration::from_secs(3600));
                }
            }
        }
    }

    let oracle = build_oracle(oracle_name, decode_table)?;
    let n = end - start;
    let words: Vec<u32> = (start..end).map(|w| w as u32).collect();
    let mut bits = vec![0u8; n.div_ceil(8) as usize];
    oracle.decode_batch_valid(&words, &mut bits, 0);
    std::fs::write(result_file, &bits).map_err(|e| format!("write result file: {e}"))
}

pub struct ShardOutcome {
    pub record: ShardRecord,
    pub oracle_bits: BTreeMap<String, Vec<u8>>,
}

fn build_shard_outcome(
    shard_id: u32,
    started: Instant,
    jobs: &[BatchJob],
    outcomes: Vec<crate::isolate::BatchOutcome>,
) -> Result<ShardOutcome, String> {
    if outcomes.len() != jobs.len() {
        return Err(format!(
            "got {} batch outcomes for {} jobs",
            outcomes.len(),
            jobs.len()
        ));
    }

    let (start, end) = shard_range(shard_id);
    let mut valid_counts = BTreeMap::new();
    let mut oracle_bits = BTreeMap::new();
    let mut total_crashes = 0u64;

    for &oracle in ORACLES.iter() {
        let mut bits = vec![0u8; (end - start).div_ceil(8) as usize];
        let mut crashed = 0u64;
        for (job, outcome) in jobs.iter().zip(outcomes.iter()) {
            if job.oracle != oracle {
                continue;
            }
            merge_batch_bits(
                &mut bits,
                job.start - start,
                &outcome.bits,
                job.end - job.start,
            );
            crashed += outcome.crashed.len() as u64;
        }
        valid_counts.insert(oracle.to_owned(), silica_bitmap::popcount_bytes(&bits));
        total_crashes += crashed;
        oracle_bits.insert(oracle.to_owned(), bits);
    }

    let mut hasher = Sha256::new();
    for &oracle in ORACLES.iter() {
        hasher.update(&oracle_bits[oracle]);
    }
    let content_hash = hex::encode(hasher.finalize());
    let status = if total_crashes == 0 {
        "complete"
    } else {
        "crashed"
    };
    let record = ShardRecord {
        shard_id,
        start,
        end,
        oracles: ORACLES.iter().map(|s| s.to_string()).collect(),
        valid_counts,
        crash_count: total_crashes,
        untriaged_crash_count: total_crashes,
        content_hash,
        duration_ms: started.elapsed().as_millis() as u64,
        status: status.to_owned(),
    };
    Ok(ShardOutcome {
        record,
        oracle_bits,
    })
}

fn sweep_shard_parallel(
    shard_id: u32,
    decode_table: &Path,
    self_exe: &Path,
    scratch_dir: &Path,
) -> Result<ShardOutcome, String> {
    let started = Instant::now();
    let (start, end) = shard_range(shard_id);
    let workers = std::thread::available_parallelism()
        .map(usize::from)
        .unwrap_or(1);
    let jobs = verification_jobs(start, end, workers)?;
    let outcomes = execute_jobs(&jobs, workers, &|job| {
        run_batch_isolated(
            self_exe,
            job.oracle,
            job.start,
            job.end,
            decode_table,
            scratch_dir,
        )
    })?;
    build_shard_outcome(shard_id, started, &jobs, outcomes)
}

// runs one shard against all four oracles with process-level crash
// isolation, batched at DEFAULT_BATCH_BITS words per worker invocation
pub fn sweep_shard(
    shard_id: u32,
    decode_table: &Path,
    self_exe: &Path,
    scratch_dir: &Path,
) -> Result<ShardOutcome, String> {
    let (start, end) = shard_range(shard_id);
    let t0 = Instant::now();

    let mut valid_counts = BTreeMap::new();
    let mut oracle_bits = BTreeMap::new();
    let mut total_crashes: u64 = 0;

    for &oracle in ORACLES.iter() {
        let mut bits = vec![0u8; (end - start).div_ceil(8) as usize];
        let mut crashed = 0u64;
        let mut batch_start = start;
        while batch_start < end {
            let batch_end = (batch_start + DEFAULT_BATCH_BITS).min(end);
            let outcome = run_batch_isolated(
                self_exe,
                oracle,
                batch_start,
                batch_end,
                decode_table,
                scratch_dir,
            )?;
            crashed += outcome.crashed.len() as u64;
            let rel_start = batch_start - start;
            for i in 0..(batch_end - batch_start) {
                let bit = rel_start + i;
                let byte_from = outcome.bits[(i / 8) as usize];
                if (byte_from >> (i % 8)) & 1 == 1 {
                    bits[(bit / 8) as usize] |= 1 << (bit % 8);
                }
            }
            batch_start = batch_end;
        }
        let popcount = silica_bitmap::popcount_bytes(&bits);
        valid_counts.insert(oracle.to_string(), popcount);
        total_crashes += crashed;
        oracle_bits.insert(oracle.to_string(), bits);
    }

    let mut hasher = Sha256::new();
    for &oracle in ORACLES.iter() {
        hasher.update(&oracle_bits[oracle]);
    }
    let content_hash = hex::encode(hasher.finalize());

    let duration_ms = t0.elapsed().as_millis() as u64;
    let status = if total_crashes == 0 {
        "complete"
    } else {
        "crashed"
    };

    let record = ShardRecord {
        shard_id,
        start,
        end,
        oracles: ORACLES.iter().map(|s| s.to_string()).collect(),
        valid_counts,
        crash_count: total_crashes,
        untriaged_crash_count: total_crashes,
        content_hash,
        duration_ms,
        status: status.to_string(),
    };

    Ok(ShardOutcome {
        record,
        oracle_bits,
    })
}

fn shard_json_path(out: &Path, shard_id: u32) -> PathBuf {
    out.join("sweep")
        .join("shards")
        .join(format!("{shard_id:03}.json"))
}

fn write_record(out: &Path, shard_id: u32, record: &ShardRecord) -> Result<(), String> {
    let path = shard_json_path(out, shard_id);
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let json = serde_json::to_string_pretty(record).map_err(|e| e.to_string())?;
    std::fs::write(path, json).map_err(|e| e.to_string())
}

// a shard already recorded complete is skippable, but only if the real
// bitmap contents still match what the record claims -- otherwise this
// would silently hide a corrupt or partially-overwritten prior run
fn already_complete(out: &Path, shard_id: u32) -> Option<bool> {
    let path = shard_json_path(out, shard_id);
    let text = std::fs::read_to_string(&path).ok()?;
    let record: ShardRecord = serde_json::from_str(&text).ok()?;
    if record.status != "complete" {
        return Some(false);
    }
    let (start, end) = shard_range(shard_id);
    for &oracle in ORACLES.iter() {
        let bmp_path = out.join("bitmaps").join(format!("{oracle}.bin"));
        let bmp = Bitmap::open_or_create(&bmp_path, TOTAL_BITS).ok()?;
        let actual = bmp.popcount_range(start, end);
        let recorded = *record.valid_counts.get(oracle)?;
        if actual != recorded {
            return Some(false);
        }
    }
    Some(true)
}

pub fn run_command(
    shard_id: u32,
    decode_table: &Path,
    out: &Path,
    self_exe: &Path,
) -> Result<(), String> {
    if let Some(true) = already_complete(out, shard_id) {
        eprintln!("shard {shard_id} already complete and bitmap-verified, skipping");
        return Ok(());
    }

    // per-shard scratch dir: `run` on shard N is meant to be invoked
    // concurrently with `run` on every other shard against the same --out
    // dir (that's the whole point of sharding). a shared ".worker-tmp"
    // meant one shard's cleanup (remove_dir_all) could delete another
    // still-running shard's in-flight result files -- caused real,
    // reproducible failures under `xargs -P 16` against one --out dir.
    let scratch = out.join(format!(".worker-tmp-{shard_id:03}"));
    std::fs::create_dir_all(&scratch).map_err(|e| e.to_string())?;
    let outcome = sweep_shard(shard_id, decode_table, self_exe, &scratch)?;
    let _ = std::fs::remove_dir_all(&scratch);

    std::fs::create_dir_all(out.join("bitmaps")).map_err(|e| e.to_string())?;
    let (start, end) = shard_range(shard_id);
    for &oracle in ORACLES.iter() {
        let bmp_path = out.join("bitmaps").join(format!("{oracle}.bin"));
        let mut bmp = Bitmap::open_or_create(&bmp_path, TOTAL_BITS).map_err(|e| e.to_string())?;
        bmp.byte_range_mut(start, end)
            .copy_from_slice(&outcome.oracle_bits[oracle]);
        bmp.flush().map_err(|e| e.to_string())?;
    }

    write_record(out, shard_id, &outcome.record)
}

pub fn verify_shard_command(
    shard_id: u32,
    decode_table: &Path,
    out: &Path,
    self_exe: &Path,
) -> Result<(), String> {
    let scratch = out.join(format!(".worker-tmp-{shard_id:03}"));
    std::fs::create_dir_all(&scratch).map_err(|e| e.to_string())?;
    let outcome = sweep_shard_parallel(shard_id, decode_table, self_exe, &scratch)?;
    let _ = std::fs::remove_dir_all(&scratch);

    std::fs::create_dir_all(out.join("bitmaps")).map_err(|e| e.to_string())?;
    for &oracle in ORACLES.iter() {
        let path = out
            .join("bitmaps")
            .join(format!("{oracle}-{shard_id:03}.bin"));
        std::fs::write(path, &outcome.oracle_bits[oracle]).map_err(|e| e.to_string())?;
    }

    write_record(out, shard_id, &outcome.record)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::isolate::BatchOutcome;
    use crate::SHARD_BITS;

    #[test]
    fn outcome_builder_keeps_oracle_order_counts_and_crashes() {
        let jobs = verification_jobs(0, SHARD_BITS, 4).unwrap();
        let outcomes = jobs
            .iter()
            .map(|job| {
                let mut bits = vec![0u8; (job.end - job.start).div_ceil(8) as usize];
                bits[0] = if job.oracle == "spec" { 0b11 } else { 0b01 };
                BatchOutcome {
                    bits,
                    crashed: if job.oracle == "unicorn" && job.start == 0 {
                        vec![4]
                    } else {
                        Vec::new()
                    },
                }
            })
            .collect();

        let outcome = build_shard_outcome(0, Instant::now(), &jobs, outcomes).unwrap();
        assert_eq!(outcome.record.oracles, ORACLES);
        assert_eq!(outcome.record.valid_counts["capstone"], 4);
        assert_eq!(outcome.record.valid_counts["llvm"], 4);
        assert_eq!(outcome.record.valid_counts["spec"], 8);
        assert_eq!(outcome.record.valid_counts["unicorn"], 4);
        assert_eq!(outcome.record.crash_count, 1);
        assert_eq!(outcome.record.untriaged_crash_count, 1);
        assert_eq!(outcome.record.status, "crashed");
        assert_eq!(outcome.oracle_bits.len(), ORACLES.len());
    }

    #[test]
    fn outcome_builder_rejects_missing_batch_result() {
        let jobs = verification_jobs(0, SHARD_BITS, 4).unwrap();
        let error = match build_shard_outcome(0, Instant::now(), &jobs, Vec::new()) {
            Ok(_) => panic!("missing batch result unexpectedly succeeded"),
            Err(error) => error,
        };
        assert_eq!(error, "got 0 batch outcomes for 16 jobs");
    }
}
