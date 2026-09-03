use std::path::PathBuf;

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "silica-sweep")]
struct Cli {
    #[command(subcommand)]
    cmd: Commands,
}

#[derive(Subcommand)]
enum Commands {
    // sweeps shard N against all four oracles into the real artifacts/ layout
    Run {
        #[arg(long)]
        shard: u32,
        #[arg(long = "spec-decode-table")]
        spec_decode_table: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    // re-runs shard N in isolation into scratch-only small bitmap slices,
    // used by G2's verifier to reproduce a recorded hash
    VerifyShard {
        #[arg(long)]
        shard: u32,
        #[arg(long = "spec-decode-table")]
        spec_decode_table: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    // internal only: processes one oracle over one word range, invoked by
    // the parent as a subprocess so a real crash only kills this child
    Worker {
        #[arg(long)]
        oracle: String,
        #[arg(long)]
        start: u64,
        #[arg(long)]
        end: u64,
        #[arg(long = "spec-decode-table")]
        spec_decode_table: PathBuf,
        #[arg(long = "result-file")]
        result_file: PathBuf,
    },
    // G4 tier 1: exhaustive validity-disagreement extraction from the
    // already-swept bitmaps, plus a reservoir sample of all-four-valid
    // words for tier 2 (see g4.rs)
    G4Tier1 {
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        scratch: PathBuf,
        #[arg(long, default_value_t = 20000)]
        sample_size: usize,
        #[arg(long, default_value_t = 0xC0FFEE)]
        seed: u64,
    },
    // G4 tier 2 support: disassemble a word list through all four oracles
    G4Disasm {
        #[arg(long)]
        words: PathBuf,
        #[arg(long = "spec-decode-table")]
        spec_decode_table: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    // native, multicore schema audit for the published G4 corpus
    G4ValidateCorpus {
        #[arg(long)]
        corpus: PathBuf,
        #[arg(long, default_value_t = 0)]
        workers: usize,
        #[arg(long, default_value = "zstd")]
        zstd: PathBuf,
    },
}

fn main() {
    let cli = Cli::parse();
    let self_exe = std::env::current_exe().expect("current_exe");

    let result = match cli.cmd {
        Commands::Run {
            shard,
            spec_decode_table,
            out,
        } => silica_sweep::sweep::run_command(shard, &spec_decode_table, &out, &self_exe),
        Commands::VerifyShard {
            shard,
            spec_decode_table,
            out,
        } => silica_sweep::sweep::verify_shard_command(shard, &spec_decode_table, &out, &self_exe),
        Commands::Worker {
            oracle,
            start,
            end,
            spec_decode_table,
            result_file,
        } => {
            silica_sweep::sweep::worker_main(&oracle, start, end, &spec_decode_table, &result_file)
        }
        Commands::G4Tier1 {
            out,
            scratch,
            sample_size,
            seed,
        } => silica_sweep::g4::run_tier1(&out, &scratch, sample_size, seed).map(|s| {
            eprintln!(
                "tier1: {} validity disagreements across {} shards, {} all-four-valid words",
                s.validity_disagreements, s.shards_with_disagreements, s.all_valid_population
            );
        }),
        Commands::G4Disasm {
            words,
            spec_decode_table,
            out,
        } => silica_sweep::g4_disasm::run_disasm(&words, &spec_decode_table, &out).map(|n| {
            eprintln!("disasm: {n} words processed");
        }),
        Commands::G4ValidateCorpus {
            corpus,
            workers,
            zstd,
        } => silica_sweep::g4_validate::validate_corpus(&corpus, workers, &zstd).and_then(
            |summary| {
                println!(
                    "{}",
                    serde_json::to_string(&summary).map_err(|e| e.to_string())?
                );
                if summary.problem.is_some() {
                    Err("corpus schema validation failed".to_owned())
                } else {
                    Ok(())
                }
            },
        ),
    };

    if let Err(e) = result {
        eprintln!("silica-sweep: error: {e}");
        std::process::exit(1);
    }
}
