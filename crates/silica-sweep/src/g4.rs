// G4 tier 1: exhaustive validity-disagreement extraction straight from the
// four already-swept bitmaps, plus a reservoir sample of "all four agree
// valid" words for the text-tier (tier 2) throughput measurement/sampling
// done separately in g4-disasm. Design.md §6: this needs no disassembly at
// all, just a read of four 512MiB files -- the cheap, exhaustive tier.

use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{BufWriter, Write};
use std::path::Path;

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use serde::Serialize;
use silica_bitmap::Bitmap;

use crate::{shard_range, N_SHARDS, ORACLES, TOTAL_BITS};

#[derive(Serialize)]
struct ValidityRecord<'a> {
    format_version: u32,
    word: String,
    category: &'a str,
    oracle_valid: BTreeMap<&'a str, bool>,
    oracle_text: BTreeMap<&'a str, Option<String>>,
}

pub struct Tier1Summary {
    pub validity_disagreements: u64,
    pub shards_with_disagreements: u64,
    pub all_valid_population: u64,
}

// reservoir sampling (Algorithm R): keeps a uniform sample of `k` items
// seen so far out of an arbitrarily long stream, one pass, O(k) memory.
struct Reservoir {
    items: Vec<u32>,
    k: usize,
    seen: u64,
    rng: StdRng,
}

impl Reservoir {
    fn new(k: usize, seed: u64) -> Self {
        Reservoir {
            items: Vec::with_capacity(k),
            k,
            seen: 0,
            rng: StdRng::seed_from_u64(seed),
        }
    }

    fn offer(&mut self, word: u32) {
        self.seen += 1;
        if self.items.len() < self.k {
            self.items.push(word);
        } else {
            let j = self.rng.gen_range(0..self.seen);
            if (j as usize) < self.k {
                self.items[j as usize] = word;
            }
        }
    }
}

pub fn run_tier1(
    out: &Path,
    scratch: &Path,
    sample_size: usize,
    seed: u64,
) -> Result<Tier1Summary, String> {
    let bitmaps: BTreeMap<&str, Bitmap> = ORACLES
        .iter()
        .map(|&o| {
            let path = out.join("bitmaps").join(format!("{o}.bin"));
            Bitmap::open_or_create(&path, TOTAL_BITS)
                .map(|b| (o, b))
                .map_err(|e| format!("open {o} bitmap: {e}"))
        })
        .collect::<Result<_, String>>()?;

    fs::create_dir_all(scratch.join("tier1")).map_err(|e| e.to_string())?;

    let mut reservoir = Reservoir::new(sample_size, seed);
    let mut validity_disagreements: u64 = 0;
    let mut shards_with_disagreements: u64 = 0;
    let mut all_valid_population: u64 = 0;

    for shard_id in 0..N_SHARDS {
        let (start, end) = shard_range(shard_id);
        let bytes: BTreeMap<&str, &[u8]> = ORACLES
            .iter()
            .map(|&o| (o, bitmaps[o].byte_range(start, end)))
            .collect();

        let mut lines: Vec<String> = Vec::new();
        for (i, (((&cap, &llvm), &spec), &uni)) in bytes["capstone"]
            .iter()
            .zip(bytes["llvm"].iter())
            .zip(bytes["spec"].iter())
            .zip(bytes["unicorn"].iter())
            .enumerate()
        {
            let agree_valid_all = cap & llvm & spec & uni;
            let agree_invalid_all = !cap & !llvm & !spec & !uni;
            let disagree = !(agree_valid_all | agree_invalid_all);

            all_valid_population += agree_valid_all.count_ones() as u64;

            let mut bit = agree_valid_all;
            while bit != 0 {
                let b = bit.trailing_zeros();
                let word = start + (i as u64) * 8 + b as u64;
                reservoir.offer(word as u32);
                bit &= bit - 1;
            }

            if disagree == 0 {
                continue;
            }
            let mut bit = disagree;
            while bit != 0 {
                let b = bit.trailing_zeros();
                let word = start + (i as u64) * 8 + b as u64;
                let get = |byte: u8| (byte >> b) & 1 == 1;
                let mut oracle_valid = BTreeMap::new();
                oracle_valid.insert("capstone", get(cap));
                oracle_valid.insert("llvm", get(llvm));
                oracle_valid.insert("spec", get(spec));
                oracle_valid.insert("unicorn", get(uni));
                let mut oracle_text = BTreeMap::new();
                for &o in ORACLES.iter() {
                    oracle_text.insert(o, None);
                }
                let rec = ValidityRecord {
                    format_version: 1,
                    word: format!("0x{word:08x}"),
                    category: "VALIDITY",
                    oracle_valid,
                    oracle_text,
                };
                lines.push(serde_json::to_string(&rec).map_err(|e| e.to_string())?);
                validity_disagreements += 1;
                bit &= bit - 1;
            }
        }

        if !lines.is_empty() {
            shards_with_disagreements += 1;
            let path = scratch.join("tier1").join(format!("{shard_id:03}.jsonl"));
            let f = File::create(&path).map_err(|e| e.to_string())?;
            let mut w = BufWriter::new(f);
            for l in &lines {
                writeln!(w, "{l}").map_err(|e| e.to_string())?;
            }
        }
    }

    let candidates = serde_json::json!({
        "population_all_valid": all_valid_population,
        "sample_seen": reservoir.seen,
        "sample": reservoir.items.iter().map(|w| format!("0x{w:08x}")).collect::<Vec<_>>(),
    });
    fs::write(
        scratch.join("tier2_candidates.json"),
        serde_json::to_string_pretty(&candidates).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;

    Ok(Tier1Summary {
        validity_disagreements,
        shards_with_disagreements,
        all_valid_population,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    // full-size (512MiB) sparse bitmaps -- open_or_create zero-fills via
    // set_len, which is sparse on any normal filesystem, so this test is
    // fast despite the files being logically 2**32 bits each.
    #[test]
    fn finds_known_disagreement_and_all_valid_population() {
        let out = tempdir().unwrap();
        let scratch = tempdir().unwrap();
        fs::create_dir_all(out.path().join("bitmaps")).unwrap();

        // word 0: capstone/llvm/spec valid, unicorn not -- a VALIDITY disagreement
        // word 8 (byte 1): all four valid -- contributes to all_valid_population
        for (oracle, set_word0, set_word8) in [
            ("capstone", true, true),
            ("llvm", true, true),
            ("spec", true, true),
            ("unicorn", false, true),
        ] {
            let mut bm = Bitmap::open_or_create(
                out.path().join("bitmaps").join(format!("{oracle}.bin")),
                TOTAL_BITS,
            )
            .unwrap();
            if set_word0 {
                bm.set_bit(0);
            }
            if set_word8 {
                bm.set_bit(8);
            }
            bm.flush().unwrap();
        }

        let summary = run_tier1(out.path(), scratch.path(), 100, 1).unwrap();
        assert_eq!(summary.validity_disagreements, 1);
        assert_eq!(summary.shards_with_disagreements, 1);
        assert_eq!(summary.all_valid_population, 1);

        let shard0 = fs::read_to_string(scratch.path().join("tier1").join("000.jsonl")).unwrap();
        let rec: serde_json::Value = serde_json::from_str(shard0.trim()).unwrap();
        assert_eq!(rec["word"], "0x00000000");
        assert_eq!(rec["category"], "VALIDITY");
        assert_eq!(rec["oracle_valid"]["unicorn"], false);
        assert_eq!(rec["oracle_valid"]["capstone"], true);

        let candidates: serde_json::Value = serde_json::from_str(
            &fs::read_to_string(scratch.path().join("tier2_candidates.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(candidates["population_all_valid"], 1);
        assert_eq!(candidates["sample"][0], "0x00000008");
    }
}
