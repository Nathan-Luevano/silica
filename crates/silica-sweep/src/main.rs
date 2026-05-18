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
    };

    if let Err(e) = result {
        eprintln!("silica-sweep: error: {e}");
        std::process::exit(1);
    }
}
