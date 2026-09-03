use std::sync::atomic::{AtomicUsize, Ordering};

use crate::{DEFAULT_BATCH_BITS, ORACLES};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct BatchJob {
    pub oracle: &'static str,
    pub start: u64,
    pub end: u64,
}

fn aligned_batch_bits(start: u64, end: u64, workers: usize) -> Result<u64, String> {
    if workers == 0 {
        return Err("worker count must be greater than zero".to_owned());
    }
    let word_count = end
        .checked_sub(start)
        .ok_or_else(|| format!("invalid batch range: {start}..{end}"))?;
    if word_count == 0 {
        return Ok(DEFAULT_BATCH_BITS);
    }

    let timeout_batches = word_count.div_ceil(DEFAULT_BATCH_BITS);
    let target_batches = (workers as u64).max(timeout_batches);
    let raw_bits = word_count.div_ceil(target_batches);
    let byte_aligned = raw_bits.div_ceil(8) * 8;
    Ok(byte_aligned.clamp(8, DEFAULT_BATCH_BITS))
}

pub(crate) fn verification_jobs(
    start: u64,
    end: u64,
    workers: usize,
) -> Result<Vec<BatchJob>, String> {
    let batch_bits = aligned_batch_bits(start, end, workers)?;
    let mut jobs = Vec::new();
    for &oracle in ORACLES.iter() {
        let mut batch_start = start;
        while batch_start < end {
            let batch_end = (batch_start + batch_bits).min(end);
            jobs.push(BatchJob {
                oracle,
                start: batch_start,
                end: batch_end,
            });
            batch_start = batch_end;
        }
    }
    Ok(jobs)
}

pub(crate) fn execute_jobs<T, F>(
    jobs: &[BatchJob],
    worker_count: usize,
    run: &F,
) -> Result<Vec<T>, String>
where
    T: Send,
    F: Fn(BatchJob) -> Result<T, String> + Sync,
{
    if worker_count == 0 {
        return Err("worker count must be greater than zero".to_owned());
    }
    if jobs.is_empty() {
        return Ok(Vec::new());
    }

    let next = AtomicUsize::new(0);
    let threads = worker_count.min(jobs.len());
    let mut indexed = Vec::new();
    std::thread::scope(|scope| -> Result<(), String> {
        let mut handles = Vec::with_capacity(threads);
        for _ in 0..threads {
            handles.push(scope.spawn(|| {
                let mut local = Vec::new();
                loop {
                    let index = next.fetch_add(1, Ordering::Relaxed);
                    let Some(&job) = jobs.get(index) else {
                        break;
                    };
                    local.push((index, run(job)));
                }
                local
            }));
        }
        for handle in handles {
            let mut local = handle
                .join()
                .map_err(|_| "batch worker thread panicked".to_owned())?;
            indexed.append(&mut local);
        }
        Ok(())
    })?;

    indexed.sort_unstable_by_key(|(index, _)| *index);
    if indexed.len() != jobs.len() {
        return Err(format!(
            "batch scheduler returned {} results for {} jobs",
            indexed.len(),
            jobs.len()
        ));
    }
    indexed
        .into_iter()
        .enumerate()
        .map(|(expected, (actual, result))| {
            if actual != expected {
                return Err(format!(
                    "batch scheduler result gap: expected {expected}, got {actual}"
                ));
            }
            result
        })
        .collect()
}

pub(crate) fn merge_batch_bits(target: &mut [u8], target_bit: u64, source: &[u8], bit_count: u64) {
    if target_bit.is_multiple_of(8) && bit_count.is_multiple_of(8) {
        let byte_start = (target_bit / 8) as usize;
        let byte_end = byte_start + (bit_count / 8) as usize;
        target[byte_start..byte_end].copy_from_slice(source);
        return;
    }
    for i in 0..bit_count {
        if (source[(i / 8) as usize] >> (i % 8)) & 1 == 1 {
            let bit = target_bit + i;
            target[(bit / 8) as usize] |= 1 << (bit % 8);
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::Duration;

    use super::*;
    use crate::SHARD_BITS;

    fn small_jobs(count: usize) -> Vec<BatchJob> {
        (0..count)
            .map(|index| BatchJob {
                oracle: "spec",
                start: index as u64,
                end: index as u64 + 1,
            })
            .collect()
    }

    #[test]
    fn thirty_two_workers_keep_thirty_two_jobs_per_oracle() {
        let jobs = verification_jobs(0, SHARD_BITS, 32).unwrap();
        assert_eq!(jobs.len(), 32 * ORACLES.len());
        for (oracle_index, oracle) in ORACLES.iter().enumerate() {
            let oracle_jobs = &jobs[oracle_index * 32..oracle_index * 32 + 32];
            assert!(oracle_jobs.iter().all(|job| job.oracle == *oracle));
            assert!(oracle_jobs
                .iter()
                .all(|job| job.end - job.start == SHARD_BITS / 32));
        }
    }

    #[test]
    fn one_worker_still_uses_timeout_safe_batches() {
        let jobs = verification_jobs(0, SHARD_BITS, 1).unwrap();
        assert_eq!(jobs.len(), 4 * ORACLES.len());
        assert!(jobs
            .iter()
            .all(|job| job.end - job.start <= DEFAULT_BATCH_BITS));
    }

    #[test]
    fn irregular_range_is_tiled_without_gaps() {
        let start = 13;
        let end = start + DEFAULT_BATCH_BITS * 2 + 19;
        let jobs = verification_jobs(start, end, 3).unwrap();
        let per_oracle = jobs.len() / ORACLES.len();
        for oracle_index in 0..ORACLES.len() {
            let oracle_jobs =
                &jobs[oracle_index * per_oracle..oracle_index * per_oracle + per_oracle];
            assert_eq!(oracle_jobs.first().unwrap().start, start);
            assert_eq!(oracle_jobs.last().unwrap().end, end);
            for pair in oracle_jobs.windows(2) {
                assert_eq!(pair[0].end, pair[1].start);
            }
        }
    }

    #[test]
    fn very_high_worker_count_keeps_nonempty_byte_sized_jobs() {
        let jobs = verification_jobs(100, 117, 1000).unwrap();
        assert_eq!(jobs.len(), 3 * ORACLES.len());
        for oracle_index in 0..ORACLES.len() {
            let oracle_jobs = &jobs[oracle_index * 3..oracle_index * 3 + 3];
            assert_eq!(oracle_jobs[0].end - oracle_jobs[0].start, 8);
            assert_eq!(oracle_jobs[1].end - oracle_jobs[1].start, 8);
            assert_eq!(oracle_jobs[2].end - oracle_jobs[2].start, 1);
        }
    }

    #[test]
    fn empty_range_has_no_jobs() {
        assert!(verification_jobs(42, 42, 4).unwrap().is_empty());
    }

    #[test]
    fn reversed_range_is_rejected() {
        assert_eq!(
            verification_jobs(43, 42, 4).unwrap_err(),
            "invalid batch range: 43..42"
        );
    }

    #[test]
    fn zero_workers_are_rejected_during_planning() {
        assert_eq!(
            verification_jobs(0, SHARD_BITS, 0).unwrap_err(),
            "worker count must be greater than zero"
        );
    }

    #[test]
    fn executor_preserves_input_order_across_out_of_order_finishes() {
        let jobs = small_jobs(12);
        let results = execute_jobs(&jobs, 4, &|job| {
            std::thread::sleep(Duration::from_millis((12 - job.start) % 4));
            Ok(job.start * 10)
        })
        .unwrap();
        assert_eq!(results, (0..12).map(|value| value * 10).collect::<Vec<_>>());
    }

    #[test]
    fn executor_uses_multiple_threads_but_honors_cap() {
        let jobs = small_jobs(18);
        let active = AtomicUsize::new(0);
        let peak = AtomicUsize::new(0);
        let results = execute_jobs(&jobs, 3, &|job| {
            let now = active.fetch_add(1, Ordering::SeqCst) + 1;
            peak.fetch_max(now, Ordering::SeqCst);
            std::thread::sleep(Duration::from_millis(5));
            active.fetch_sub(1, Ordering::SeqCst);
            Ok(job.start)
        })
        .unwrap();
        assert_eq!(results.len(), jobs.len());
        assert!(peak.load(Ordering::SeqCst) > 1);
        assert!(peak.load(Ordering::SeqCst) <= 3);
        assert_eq!(active.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn executor_does_not_spawn_more_threads_than_jobs() {
        let jobs = small_jobs(2);
        let calls = AtomicUsize::new(0);
        let results = execute_jobs(&jobs, 100, &|job| {
            calls.fetch_add(1, Ordering::SeqCst);
            Ok(job.start)
        })
        .unwrap();
        assert_eq!(results, vec![0, 1]);
        assert_eq!(calls.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn executor_reports_errors_by_stable_job_order() {
        let jobs = small_jobs(8);
        let error = execute_jobs(&jobs, 8, &|job| -> Result<u64, String> {
            if job.start == 2 {
                std::thread::sleep(Duration::from_millis(10));
                return Err("second-index-error".to_owned());
            }
            if job.start == 6 {
                return Err("later-fast-error".to_owned());
            }
            Ok(job.start)
        })
        .unwrap_err();
        assert_eq!(error, "second-index-error");
    }

    #[test]
    fn executor_rejects_zero_workers() {
        let error = execute_jobs(&small_jobs(1), 0, &|job| Ok(job.start)).unwrap_err();
        assert_eq!(error, "worker count must be greater than zero");
    }

    #[test]
    fn executor_accepts_no_jobs_without_calling_runner() {
        let calls = AtomicUsize::new(0);
        let results = execute_jobs(&[], 2, &|_: BatchJob| {
            calls.fetch_add(1, Ordering::SeqCst);
            Ok::<u64, String>(0)
        })
        .unwrap();
        assert!(results.is_empty());
        assert_eq!(calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn executor_turns_thread_panic_into_error() {
        let error = execute_jobs(&small_jobs(2), 2, &|job| -> Result<u64, String> {
            assert_ne!(job.start, 1, "intentional worker panic");
            Ok(job.start)
        })
        .unwrap_err();
        assert_eq!(error, "batch worker thread panicked");
    }

    #[test]
    fn aligned_merge_copies_complete_bytes() {
        let mut target = vec![0x55, 0x55, 0x55, 0x55];
        merge_batch_bits(&mut target, 8, &[0xaa, 0x0f], 16);
        assert_eq!(target, vec![0x55, 0xaa, 0x0f, 0x55]);
    }

    #[test]
    fn unaligned_merge_sets_only_source_one_bits() {
        let mut target = vec![0b0000_0010, 0];
        merge_batch_bits(&mut target, 3, &[0b0000_0101], 3);
        assert_eq!(target, vec![0b0010_1010, 0]);
    }
}
