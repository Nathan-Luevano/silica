// G4 tier 2 support: disassemble an arbitrary list of words (already known
// all-four-valid, from g4::run_tier1's reservoir sample) through all four
// oracles and dump raw text. classification (normalize + taxonomy) happens
// in Python against pysilica/analyze/normalize.py -- this only produces the
// raw per-oracle text, same division of labor as the rest of the project
// (Rust: fast bulk oracle work, Python: spec-derived normalization rules).
//
// Uses in-process catch_unwind isolation (decode_isolated/disassemble_isolated)
// rather than the full process-per-batch isolation in isolate.rs: these words
// already decoded successfully as valid in the original sweep with zero
// crashes recorded across all 256 shards (WORKLOG.md), so the risk profile
// here is materially different from the raw exhaustive sweep. A genuine
// native crash on one of these words would abort this whole batch -- an
// acceptable, stated tradeoff for what is a measurement/sampling pass, not
// the exhaustive tier.

use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::Path;

use crate::oracle::build_oracle;
use crate::ORACLES;

pub fn run_disasm(words_file: &Path, decode_table: &Path, out_file: &Path) -> Result<u64, String> {
    let oracles: BTreeMap<&str, Box<dyn silica_oracles::Oracle>> = ORACLES
        .iter()
        .map(|&name| build_oracle(name, decode_table).map(|o| (name, o)))
        .collect::<Result<_, String>>()?;

    let f = File::open(words_file).map_err(|e| format!("open words file: {e}"))?;
    let reader = BufReader::new(f);
    let out = File::create(out_file).map_err(|e| e.to_string())?;
    let mut w = BufWriter::new(out);

    let mut count: u64 = 0;
    for line in reader.lines() {
        let line = line.map_err(|e| e.to_string())?;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let word = u32::from_str_radix(line.trim_start_matches("0x"), 16)
            .map_err(|e| format!("bad word {line}: {e}"))?;

        let mut texts = BTreeMap::new();
        for &name in ORACLES.iter() {
            let oracle = &oracles[name];
            let text = if oracle.decode_isolated(word) {
                oracle.disassemble_isolated(word)
            } else {
                None
            };
            texts.insert(name, text);
        }
        let rec = serde_json::json!({
            "word": format!("0x{word:08x}"),
            "oracle_text": texts,
        });
        writeln!(
            w,
            "{}",
            serde_json::to_string(&rec).map_err(|e| e.to_string())?
        )
        .map_err(|e| e.to_string())?;
        count += 1;
    }
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;
    use tempfile::tempdir;

    #[test]
    fn disassembles_ret_through_all_four_oracles() {
        let decode_table = Path::new("../../artifacts/decode-table.bin");
        if !decode_table.exists() {
            return; // same skip pattern as spec::tests::test_spec_oracle_with_real_artifact
        }
        let dir = tempdir().unwrap();
        let words_path = dir.path().join("words.txt");
        std::fs::write(&words_path, "0xd65f03c0\n").unwrap();
        let out_path = dir.path().join("out.jsonl");

        let n = run_disasm(&words_path, decode_table, &out_path).unwrap();
        assert_eq!(n, 1);

        let line = std::fs::read_to_string(&out_path).unwrap();
        let rec: serde_json::Value = serde_json::from_str(line.trim()).unwrap();
        assert_eq!(rec["word"], "0xd65f03c0");
        // unicorn never produces real text -- see module comment
        assert_eq!(rec["oracle_text"]["unicorn"], "<valid>");
        assert!(rec["oracle_text"]["spec"]
            .as_str()
            .unwrap()
            .to_lowercase()
            .contains("ret"));
    }
}
