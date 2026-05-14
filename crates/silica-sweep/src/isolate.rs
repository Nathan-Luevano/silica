use std::path::{Path, PathBuf};
use std::process::Command;

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

    let status = Command::new(self_exe)
        .arg("worker")
        .arg("--oracle")
        .arg(oracle)
        .arg("--start")
        .arg(start.to_string())
        .arg("--end")
        .arg(end.to_string())
        .arg("--spec-decode-table")
        .arg(decode_table)
        .arg("--result-file")
        .arg(&result_file)
        .status()
        .map_err(|e| format!("failed to spawn worker: {e}"))?;

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
