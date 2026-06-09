use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

// first cut of this constant (15s) was wrong: it assumed the P3-measured
// throughput (464,100 encodings/sec) applies per-oracle, but that figure is
// the *blended* rate across all four -- capstone/llvm/spec are near-instant,
// unicorn (real instruction emulation, not just disassembly) dominates the
// wall time. measured directly: a genuinely non-hung 1,048,576-word unicorn
// batch takes ~72s (~14,500 words/sec unicorn-only). DEFAULT_BATCH_BITS is
// 2**22 = 4,194,304 words, so a legitimate top-level unicorn batch needs
// ~290s. 15s was timing out on every single unicorn batch regardless of any
// real hang, triggering needless bisection at every level. 600s gives ~2x
// margin over the measured legitimate worst case while still bounding a
// truly infinite hang (which is what this exists to catch in the first
// place -- see WORKLOG.md for the real one found running the actual sweep).
const BATCH_TIMEOUT: Duration = Duration::from_secs(600);

// spawn + poll instead of Command::status(), so a hung child (alive, not
// crashed, just never returning -- distinct from the SIGSEGV case above)
// gets killed on a deadline instead of blocking this call forever.
fn run_with_timeout(
    mut cmd: Command,
    timeout: Duration,
) -> Result<Option<std::process::ExitStatus>, String> {
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn worker: {e}"))?;
    let started = Instant::now();
    loop {
        match child.try_wait().map_err(|e| format!("wait failed: {e}"))? {
            Some(status) => return Ok(Some(status)),
            None => {
                if started.elapsed() > timeout {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Ok(None);
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
    }
}

// bit i (0-indexed, LSB-first) of buf corresponds to word start+i in the
// batch that produced it
fn get_bit(buf: &[u8], i: u64) -> bool {
    (buf[(i / 8) as usize] >> (i % 8)) & 1 == 1
}

fn set_bit(buf: &mut [u8], i: u64) {
    buf[(i / 8) as usize] |= 1 << (i % 8);
}

pub struct BatchOutcome {
    // packed bits, length ceil((end-start)/8) bytes, bit i == word start+i
    pub bits: Vec<u8>,
    // absolute word values where the worker process died (signal/nonzero exit)
    pub crashed: Vec<u64>,
}

// runs [start, end) for one oracle through a fresh child process. if the
// child dies (segfault, panic-abort, whatever), bisects the range and
// retries each half, isolating down to the single crashing word rather
// than losing the whole batch. this is what survives a real SIGSEGV in
// libcapstone/libLLVM/libunicorn -- catch_unwind cannot.
pub fn run_batch_isolated(
    self_exe: &Path,
    oracle: &str,
    start: u64,
    end: u64,
    decode_table: &Path,
    scratch_dir: &Path,
) -> Result<BatchOutcome, String> {
    let n = end - start;
    let result_file: PathBuf = scratch_dir.join(format!("worker-{oracle}-{start}-{end}.raw"));

    let mut cmd = Command::new(self_exe);
    cmd.arg("worker")
        .arg("--oracle")
        .arg(oracle)
        .arg("--start")
        .arg(start.to_string())
        .arg("--end")
        .arg(end.to_string())
        .arg("--spec-decode-table")
        .arg(decode_table)
        .arg("--result-file")
        .arg(&result_file);

    let status = match run_with_timeout(cmd, BATCH_TIMEOUT)? {
        Some(s) => s,
        None => {
            // timed out and was killed -- treat exactly like a crash below,
            // same bisection recovery, just a different root cause
            let _ = std::fs::remove_file(&result_file);
            if n == 1 {
                return Ok(BatchOutcome {
                    bits: vec![0u8],
                    crashed: vec![start],
                });
            }
            let mid = start + n / 2;
            let left = run_batch_isolated(self_exe, oracle, start, mid, decode_table, scratch_dir)?;
            let right = run_batch_isolated(self_exe, oracle, mid, end, decode_table, scratch_dir)?;
            let mut bits = vec![0u8; n.div_ceil(8) as usize];
            for i in 0..(mid - start) {
                if get_bit(&left.bits, i) {
                    set_bit(&mut bits, i);
                }
            }
            for i in 0..(end - mid) {
                if get_bit(&right.bits, i) {
                    set_bit(&mut bits, (mid - start) + i);
                }
            }
            let mut crashed = left.crashed;
            crashed.extend(right.crashed);
            return Ok(BatchOutcome { bits, crashed });
        }
    };

    if status.success() {
        let bits = std::fs::read(&result_file)
            .map_err(|e| format!("worker succeeded but result file unreadable: {e}"))?;
        let _ = std::fs::remove_file(&result_file);
        let want_bytes = n.div_ceil(8) as usize;
        if bits.len() != want_bytes {
            return Err(format!(
                "worker result size mismatch: got {} bytes, wanted {}",
                bits.len(),
                want_bytes
            ));
        }
        return Ok(BatchOutcome {
            bits,
            crashed: vec![],
        });
    }

    let _ = std::fs::remove_file(&result_file);

    if n == 1 {
        // this single word killed the worker process -- record it as a
        // crash, contribute a 0 (invalid) bit, and move on
        return Ok(BatchOutcome {
            bits: vec![0u8],
            crashed: vec![start],
        });
    }

    let mid = start + n / 2;
    let left = run_batch_isolated(self_exe, oracle, start, mid, decode_table, scratch_dir)?;
    let right = run_batch_isolated(self_exe, oracle, mid, end, decode_table, scratch_dir)?;

    let mut bits = vec![0u8; n.div_ceil(8) as usize];
    for i in 0..(mid - start) {
        if get_bit(&left.bits, i) {
            set_bit(&mut bits, i);
        }
    }
    for i in 0..(end - mid) {
        if get_bit(&right.bits, i) {
            set_bit(&mut bits, (mid - start) + i);
        }
    }
    let mut crashed = left.crashed;
    crashed.extend(right.crashed);
    Ok(BatchOutcome { bits, crashed })
}
