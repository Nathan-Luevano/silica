// proves process-level isolation survives a real SIGSEGV, not just a Rust
// panic (catch_unwind cannot catch a segfault inside libcapstone/libLLVM/
// libunicorn -- see silica-oracles P2 notes). uses the spec oracle since it
// needs no vendor libs, only the already-committed decode-table.bin.

use std::path::Path;

// both cases share one test function -- SILICA_SWEEP_TEST_CRASH_WORD is a
// process-global env var inherited by spawned children, so running these
// as separate #[test] fns risks the harness's default thread-parallel
// execution racing one test's env against the other's subprocess spawns
#[test]
fn crash_isolation_bisects_and_recovers() {
    let decode_table = Path::new("../../artifacts/decode-table.bin");
    if !decode_table.exists() {
        eprintln!("skipping: no decode-table.bin artifact present");
        return;
    }

    let self_exe = Path::new(env!("CARGO_BIN_EXE_silica-sweep"));

    let scratch = tempfile::tempdir().unwrap();
    let outcome = silica_sweep::isolate::run_batch_isolated(
        self_exe,
        "spec",
        1000,
        1100,
        decode_table,
        scratch.path(),
    )
    .unwrap();
    assert!(outcome.crashed.is_empty());
    assert_eq!(outcome.bits.len(), 100u64.div_ceil(8) as usize);

    // TEST ONLY: this env var is the same debug crash-trigger the worker
    // checks for at runtime (src/sweep.rs worker_main), never set outside tests
    std::env::set_var("SILICA_SWEEP_TEST_CRASH_WORD", "0x41A");

    let scratch2 = tempfile::tempdir().unwrap();
    let crashed_outcome = silica_sweep::isolate::run_batch_isolated(
        self_exe,
        "spec",
        1000,
        1100,
        decode_table,
        scratch2.path(),
    )
    .expect("batch should still complete despite the crash");

    std::env::remove_var("SILICA_SWEEP_TEST_CRASH_WORD");

    assert_eq!(crashed_outcome.crashed, vec![1050]);
    assert_eq!(crashed_outcome.bits.len(), 100u64.div_ceil(8) as usize);
}
