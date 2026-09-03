use std::collections::{BTreeSet, HashMap};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::thread;

use serde::Serialize;
use serde_json::Value;

const ORACLES: [&str; 4] = ["capstone", "llvm", "spec", "unicorn"];
const TAXONOMY: [&str; 7] = [
    "VALIDITY",
    "MNEMONIC",
    "OPERAND",
    "ALIAS",
    "FORMATTING",
    "NORMALIZATION_UNCERTAIN",
    "CRASH",
];

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct CorpusProblem {
    pub file: String,
    pub line: u64,
    pub reason: String,
}

#[derive(Debug, Eq, PartialEq, Serialize)]
pub struct CorpusSummary {
    pub files: usize,
    pub records_read: u64,
    pub workers: usize,
    pub problem: Option<CorpusProblem>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub validity_sample: Option<ValiditySampleSummary>,
}

impl CorpusSummary {
    pub fn sample_has_missing_words(&self) -> bool {
        self.validity_sample
            .as_ref()
            .is_some_and(|sample| !sample.missing_words.is_empty())
    }
}

#[derive(Debug, Eq, PartialEq, Serialize)]
pub struct ValiditySampleSummary {
    pub checked: usize,
    pub real_disagreements: usize,
    pub matched_records: usize,
    pub missing_words: Vec<String>,
}

#[derive(Clone)]
struct FileResult {
    records: u64,
    problem: Option<CorpusProblem>,
    matched_words: BTreeSet<u32>,
}

fn problem(path: &Path, line: u64, reason: impl Into<String>) -> FileResult {
    FileResult {
        records: line.saturating_sub(1),
        problem: Some(CorpusProblem {
            file: path.display().to_string(),
            line,
            reason: reason.into(),
        }),
        matched_words: BTreeSet::new(),
    }
}

fn format_version_is_one(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => value.as_f64() == Some(1.0),
        _ => false,
    }
}

fn record_problem(record: &Value) -> Option<&'static str> {
    let Some(record) = record.as_object() else {
        return Some("record is not a JSON object");
    };
    if !format_version_is_one(record.get("format_version")) {
        return Some("record missing/wrong format_version");
    }
    let category_ok = record
        .get("category")
        .and_then(Value::as_str)
        .is_some_and(|category| TAXONOMY.contains(&category));
    if !category_ok {
        return Some("record category not in taxonomy");
    }
    let oracle_keys = record
        .get("oracle_valid")
        .and_then(Value::as_object)
        .map(|values| values.keys().map(String::as_str).collect::<BTreeSet<_>>());
    if oracle_keys != Some(ORACLES.into_iter().collect()) {
        return Some("record oracle_valid missing an oracle");
    }
    let word_ok = record
        .get("word")
        .and_then(Value::as_str)
        .is_some_and(|word| word.starts_with("0x"));
    if !word_ok {
        return Some("record word not a 0x-prefixed hex string");
    }
    None
}

fn validity_word(record: &Value, targets: &BTreeSet<u32>, shard_id: Option<u32>) -> Option<u32> {
    if record.get("category").and_then(Value::as_str) != Some("VALIDITY") {
        return None;
    }
    let word = record.get("word")?.as_str()?.strip_prefix("0x")?;
    let word = u32::from_str_radix(word, 16).ok()?;
    (targets.contains(&word) && shard_id == Some(word >> 24)).then_some(word)
}

fn validate_file(path: &Path, zstd: &Path, targets: &BTreeSet<u32>) -> FileResult {
    let shard_id = path
        .file_stem()
        .and_then(|stem| stem.to_str())
        .and_then(|stem| stem.parse::<u32>().ok());
    let mut child = match Command::new(zstd)
        .args(["-qdcf", "--no-pass-through", "--"])
        .arg(path)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(child) => child,
        Err(error) => return problem(path, 1, format!("could not start zstd: {error}")),
    };
    let Some(stdout) = child.stdout.take() else {
        let _ = child.kill();
        let _ = child.wait();
        return problem(path, 1, "zstd stdout pipe was not created");
    };

    let mut reader = BufReader::with_capacity(1 << 20, stdout);
    let mut line = Vec::with_capacity(256);
    let mut records = 0_u64;
    let mut matched_words = BTreeSet::new();
    loop {
        line.clear();
        let bytes = match reader.read_until(b'\n', &mut line) {
            Ok(bytes) => bytes,
            Err(error) => {
                drop(reader);
                let _ = child.kill();
                let _ = child.wait();
                return problem(
                    path,
                    records + 1,
                    format!("could not read zstd output: {error}"),
                );
            }
        };
        if bytes == 0 {
            break;
        }
        if line.iter().all(u8::is_ascii_whitespace) {
            continue;
        }
        let record: Value = match serde_json::from_slice(&line) {
            Ok(record) => record,
            Err(error) => {
                drop(reader);
                let _ = child.kill();
                let _ = child.wait();
                return problem(path, records + 1, format!("malformed JSON: {error}"));
            }
        };
        if let Some(reason) = record_problem(&record) {
            drop(reader);
            let _ = child.kill();
            let _ = child.wait();
            return problem(path, records + 1, reason);
        }
        if let Some(word) = validity_word(&record, targets, shard_id) {
            matched_words.insert(word);
        }
        records += 1;
    }
    drop(reader);

    match child.wait() {
        Ok(status) if status.success() => FileResult {
            records,
            problem: None,
            matched_words,
        },
        Ok(status) => problem(path, records + 1, format!("zstd exited with {status}")),
        Err(error) => problem(
            path,
            records + 1,
            format!("could not wait for zstd: {error}"),
        ),
    }
}

fn parse_sample_words(path: &Path) -> Result<Vec<u32>, String> {
    let file = File::open(path)
        .map_err(|error| format!("open sample words {}: {error}", path.display()))?;
    let mut words = Vec::new();
    for (index, line) in BufReader::new(file).lines().enumerate() {
        let line = line.map_err(|error| {
            format!(
                "read sample words {} line {}: {error}",
                path.display(),
                index + 1
            )
        })?;
        let text = line.trim();
        if text.is_empty() {
            continue;
        }
        let word = if let Some(hex) = text.strip_prefix("0x") {
            u32::from_str_radix(hex, 16)
        } else {
            text.parse::<u32>()
        }
        .map_err(|error| {
            format!(
                "invalid sample word {} line {}: {error}",
                path.display(),
                index + 1
            )
        })?;
        words.push(word);
    }
    if words.is_empty() {
        return Err(format!("no sample words in {}", path.display()));
    }
    Ok(words)
}

fn bitmap_bits(bitmaps: &Path, words: &[u32]) -> Result<HashMap<u32, [bool; 4]>, String> {
    let mut files = ORACLES
        .iter()
        .map(|oracle| {
            let path = bitmaps.join(format!("{oracle}.bin"));
            let file = File::open(&path)
                .map_err(|error| format!("open bitmap {}: {error}", path.display()))?;
            Ok::<(PathBuf, File), String>((path, file))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let mut cached_bytes = ORACLES.map(|_| HashMap::<u64, u8>::new());
    let mut values = HashMap::with_capacity(words.len());

    for &word in words {
        let offset = u64::from(word) / 8;
        let bit = word % 8;
        let mut valid = [false; 4];
        for (oracle_index, (path, file)) in files.iter_mut().enumerate() {
            let byte = if let Some(byte) = cached_bytes[oracle_index].get(&offset) {
                *byte
            } else {
                file.seek(SeekFrom::Start(offset))
                    .map_err(|error| format!("seek bitmap {}: {error}", path.display()))?;
                let mut byte = [0_u8; 1];
                file.read_exact(&mut byte)
                    .map_err(|error| format!("read bitmap {}: {error}", path.display()))?;
                cached_bytes[oracle_index].insert(offset, byte[0]);
                byte[0]
            };
            valid[oracle_index] = (byte >> bit) & 1 == 1;
        }
        values.insert(word, valid);
    }
    Ok(values)
}

fn sample_targets(
    bitmaps: &Path,
    sample_words: &Path,
) -> Result<(usize, usize, BTreeSet<u32>), String> {
    let words = parse_sample_words(sample_words)?;
    let bits = bitmap_bits(bitmaps, &words)?;
    let real_disagreements = words
        .iter()
        .filter(|word| {
            let valid = bits[word];
            valid.iter().any(|value| *value != valid[0])
        })
        .count();
    let disagreements = bits
        .into_iter()
        .filter_map(|(word, valid)| valid.iter().any(|value| *value != valid[0]).then_some(word))
        .collect();
    Ok((words.len(), real_disagreements, disagreements))
}

fn shard_files(corpus: &Path) -> Result<Vec<PathBuf>, String> {
    let entries = fs::read_dir(corpus)
        .map_err(|error| format!("read corpus directory {}: {error}", corpus.display()))?;
    let mut files = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|extension| extension == "zst"))
        .collect::<Vec<_>>();
    files.sort();
    if files.is_empty() {
        return Err(format!("no .zst files under {}", corpus.display()));
    }
    Ok(files)
}

pub fn validate_corpus(
    corpus: &Path,
    requested_workers: usize,
    zstd: &Path,
) -> Result<CorpusSummary, String> {
    validate_corpus_inner(corpus, requested_workers, zstd, None)
}

pub fn validate_corpus_with_sample(
    corpus: &Path,
    requested_workers: usize,
    zstd: &Path,
    bitmaps: &Path,
    sample_words: &Path,
) -> Result<CorpusSummary, String> {
    let sample = sample_targets(bitmaps, sample_words)?;
    validate_corpus_inner(corpus, requested_workers, zstd, Some(sample))
}

fn validate_corpus_inner(
    corpus: &Path,
    requested_workers: usize,
    zstd: &Path,
    sample: Option<(usize, usize, BTreeSet<u32>)>,
) -> Result<CorpusSummary, String> {
    let files = shard_files(corpus)?;
    let available = thread::available_parallelism().map_or(1, usize::from);
    let requested = if requested_workers == 0 {
        available
    } else {
        requested_workers
    };
    if requested == 0 {
        return Err("workers must be greater than zero".to_owned());
    }
    let workers = requested.min(files.len());
    let next = AtomicUsize::new(0);
    let results = Mutex::new(vec![None; files.len()]);
    let targets = sample
        .as_ref()
        .map(|(_, _, targets)| targets)
        .cloned()
        .unwrap_or_default();

    thread::scope(|scope| {
        for _ in 0..workers {
            scope.spawn(|| loop {
                let index = next.fetch_add(1, Ordering::Relaxed);
                let Some(path) = files.get(index) else {
                    break;
                };
                let result = validate_file(path, zstd, &targets);
                results.lock().expect("result mutex poisoned")[index] = Some(result);
            });
        }
    });

    let results = results
        .into_inner()
        .map_err(|_| "result mutex poisoned".to_owned())?;
    let mut records_read = 0_u64;
    let mut first_problem = None;
    let mut matched_words = BTreeSet::new();
    for result in results {
        let result = result.ok_or_else(|| "worker returned no result".to_owned())?;
        records_read += result.records;
        if first_problem.is_none() {
            first_problem = result.problem;
        }
        matched_words.extend(result.matched_words);
    }
    let validity_sample = sample.map(|(checked, real_disagreements, targets)| {
        let missing_words = targets
            .difference(&matched_words)
            .map(|word| format!("0x{word:08x}"))
            .collect::<Vec<_>>();
        ValiditySampleSummary {
            checked,
            real_disagreements,
            matched_records: targets.len() - missing_words.len(),
            missing_words,
        }
    });
    Ok(CorpusSummary {
        files: files.len(),
        records_read,
        workers,
        problem: first_problem,
        validity_sample,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::tempdir;

    fn record(category: &str, word: &str) -> String {
        serde_json::json!({
            "format_version": 1,
            "word": word,
            "category": category,
            "oracle_valid": {
                "capstone": true,
                "llvm": false,
                "spec": true,
                "unicorn": false,
            },
            "oracle_text": {
                "capstone": null,
                "llvm": null,
                "spec": null,
                "unicorn": null,
            },
        })
        .to_string()
    }

    fn compressed(path: &Path, lines: &[String]) {
        let mut child = Command::new("zstd")
            .args(["-q", "-c"])
            .stdin(Stdio::piped())
            .stdout(File::create(path).unwrap())
            .spawn()
            .unwrap();
        {
            let mut stdin = child.stdin.take().unwrap();
            for line in lines {
                writeln!(stdin, "{line}").unwrap();
            }
        }
        assert!(child.wait().unwrap().success());
    }

    use std::fs::File;

    fn sample_file(dir: &Path, lines: &[&str]) -> PathBuf {
        let path = dir.join("sample.txt");
        fs::write(&path, lines.join("\n") + "\n").unwrap();
        path
    }

    fn bitmaps(dir: &Path, values: &[(u32, [bool; 4])]) -> PathBuf {
        let path = dir.join("bitmaps");
        fs::create_dir(&path).unwrap();
        let bytes = values
            .iter()
            .map(|(word, _)| *word as usize / 8 + 1)
            .max()
            .unwrap_or(1);
        for (oracle_index, oracle) in ORACLES.iter().enumerate() {
            let mut bitmap = vec![0_u8; bytes];
            for (word, valid) in values {
                if valid[oracle_index] {
                    bitmap[*word as usize / 8] |= 1 << (word % 8);
                }
            }
            fs::write(path.join(format!("{oracle}.bin")), bitmap).unwrap();
        }
        path
    }

    #[test]
    fn validates_files_in_parallel_and_counts_records() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("001.zst"),
            &[
                record("VALIDITY", "0x01000000"),
                record("OPERAND", "0x01000001"),
            ],
        );
        compressed(
            &dir.path().join("000.zst"),
            &[record("NORMALIZATION_UNCERTAIN", "0x00000000")],
        );

        let summary = validate_corpus(dir.path(), 8, Path::new("zstd")).unwrap();
        assert_eq!(summary.files, 2);
        assert_eq!(summary.records_read, 3);
        assert_eq!(summary.workers, 2);
        assert_eq!(summary.problem, None);
    }

    #[cfg(unix)]
    #[test]
    fn validates_symlinked_shard_file() {
        use std::os::unix::fs::symlink;

        let source = tempdir().unwrap();
        let corpus = tempdir().unwrap();
        let compressed_path = source.path().join("source.zst");
        compressed(&compressed_path, &[record("VALIDITY", "0x00000000")]);
        symlink(compressed_path, corpus.path().join("000.zst")).unwrap();

        let summary = validate_corpus(corpus.path(), 1, Path::new("zstd")).unwrap();
        assert_eq!(summary.records_read, 1);
        assert_eq!(summary.problem, None);
    }

    #[test]
    fn reports_first_problem_in_sorted_file_order() {
        let dir = tempdir().unwrap();
        compressed(&dir.path().join("010.zst"), &["not json".to_owned()]);
        compressed(
            &dir.path().join("002.zst"),
            &[serde_json::json!({"format_version": 2}).to_string()],
        );

        let summary = validate_corpus(dir.path(), 2, Path::new("zstd")).unwrap();
        let problem = summary.problem.unwrap();
        assert!(problem.file.ends_with("002.zst"));
        assert_eq!(problem.line, 1);
        assert_eq!(problem.reason, "record missing/wrong format_version");
    }

    #[test]
    fn reports_record_line_after_valid_prefix() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("000.zst"),
            &[record("VALIDITY", "0x0"), "[]".to_owned()],
        );
        let summary = validate_corpus(dir.path(), 1, Path::new("zstd")).unwrap();
        assert_eq!(summary.records_read, 1);
        assert_eq!(summary.problem.unwrap().line, 2);
    }

    #[test]
    fn rejects_missing_or_extra_oracle_keys() {
        let mut value: Value = serde_json::from_str(&record("VALIDITY", "0x0")).unwrap();
        value["oracle_valid"]
            .as_object_mut()
            .unwrap()
            .remove("llvm");
        let dir = tempdir().unwrap();
        compressed(&dir.path().join("000.zst"), &[value.to_string()]);
        let summary = validate_corpus(dir.path(), 1, Path::new("zstd")).unwrap();
        assert_eq!(
            summary.problem.unwrap().reason,
            "record oracle_valid missing an oracle"
        );
    }

    #[test]
    fn rejects_unknown_category_and_bad_word() {
        let dir = tempdir().unwrap();
        compressed(&dir.path().join("000.zst"), &[record("UNKNOWN", "0x0")]);
        let summary = validate_corpus(dir.path(), 1, Path::new("zstd")).unwrap();
        assert_eq!(
            summary.problem.unwrap().reason,
            "record category not in taxonomy"
        );

        let other = tempdir().unwrap();
        compressed(&other.path().join("000.zst"), &[record("VALIDITY", "123")]);
        let summary = validate_corpus(other.path(), 1, Path::new("zstd")).unwrap();
        assert_eq!(
            summary.problem.unwrap().reason,
            "record word not a 0x-prefixed hex string"
        );
    }

    #[test]
    fn reports_invalid_compressed_input() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("000.zst"), b"not zstd").unwrap();
        let summary = validate_corpus(dir.path(), 1, Path::new("zstd")).unwrap();
        assert!(summary
            .problem
            .unwrap()
            .reason
            .starts_with("zstd exited with"));
    }

    #[test]
    fn fails_without_corpus_files() {
        let dir = tempdir().unwrap();
        assert!(validate_corpus(dir.path(), 1, Path::new("zstd"))
            .unwrap_err()
            .contains("no .zst files"));
    }

    #[test]
    fn reports_missing_decompressor() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("000.zst"), b"anything").unwrap();
        let summary = validate_corpus(dir.path(), 1, Path::new("definitely-no-zstd")).unwrap();
        assert!(summary
            .problem
            .unwrap()
            .reason
            .starts_with("could not start zstd"));
    }

    #[test]
    fn sample_matches_real_bitmap_disagreement_to_validity_record() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("000.zst"),
            &[
                record("VALIDITY", "0x00000001"),
                record("OPERAND", "0x00000008"),
            ],
        );
        let bitmap_dir = bitmaps(
            dir.path(),
            &[
                (0, [true; 4]),
                (1, [true, false, true, false]),
                (8, [true; 4]),
            ],
        );
        let words = sample_file(dir.path(), &["0x0", "1", "0x00000008"]);

        let summary =
            validate_corpus_with_sample(dir.path(), 2, Path::new("zstd"), &bitmap_dir, &words)
                .unwrap();
        assert_eq!(summary.records_read, 2);
        assert_eq!(
            summary.validity_sample,
            Some(ValiditySampleSummary {
                checked: 3,
                real_disagreements: 1,
                matched_records: 1,
                missing_words: vec![],
            })
        );
        assert!(!summary.sample_has_missing_words());
    }

    #[test]
    fn sample_reports_sorted_missing_validity_words() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("000.zst"),
            &[record("VALIDITY", "0x00000009")],
        );
        let bitmap_dir = bitmaps(
            dir.path(),
            &[
                (1, [true, false, true, false]),
                (9, [true, false, true, false]),
                (17, [true, true, false, false]),
            ],
        );
        let words = sample_file(dir.path(), &["17", "0x9", "1"]);

        let summary =
            validate_corpus_with_sample(dir.path(), 1, Path::new("zstd"), &bitmap_dir, &words)
                .unwrap();
        assert!(summary.sample_has_missing_words());
        assert_eq!(
            summary.validity_sample.unwrap(),
            ValiditySampleSummary {
                checked: 3,
                real_disagreements: 3,
                matched_records: 1,
                missing_words: vec!["0x00000001".to_owned(), "0x00000011".to_owned()],
            }
        );
    }

    #[test]
    fn duplicate_sample_words_count_each_draw_but_match_once() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("000.zst"),
            &[record("VALIDITY", "0x00000001")],
        );
        let bitmap_dir = bitmaps(dir.path(), &[(1, [true, false, true, false])]);
        let words = sample_file(dir.path(), &["1", "1", "0x1"]);

        let summary =
            validate_corpus_with_sample(dir.path(), 1, Path::new("zstd"), &bitmap_dir, &words)
                .unwrap();
        let sample = summary.validity_sample.unwrap();
        assert_eq!(sample.checked, 3);
        assert_eq!(sample.real_disagreements, 3);
        assert_eq!(sample.matched_records, 1);
        assert!(sample.missing_words.is_empty());
    }

    #[test]
    fn sample_record_in_wrong_shard_does_not_match() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("001.zst"),
            &[record("VALIDITY", "0x00000001")],
        );
        let bitmap_dir = bitmaps(dir.path(), &[(1, [true, false, true, false])]);
        let words = sample_file(dir.path(), &["1"]);

        let summary =
            validate_corpus_with_sample(dir.path(), 1, Path::new("zstd"), &bitmap_dir, &words)
                .unwrap();
        let sample = summary.validity_sample.unwrap();
        assert_eq!(sample.real_disagreements, 1);
        assert_eq!(sample.matched_records, 0);
        assert_eq!(sample.missing_words, vec!["0x00000001"]);
    }

    #[test]
    fn all_agree_sample_needs_no_corpus_record() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("000.zst"),
            &[record("OPERAND", "0x00000002")],
        );
        let bitmap_dir = bitmaps(dir.path(), &[(2, [false; 4]), (3, [true; 4])]);
        let words = sample_file(dir.path(), &["2", "3"]);

        let summary =
            validate_corpus_with_sample(dir.path(), 1, Path::new("zstd"), &bitmap_dir, &words)
                .unwrap();
        assert_eq!(
            summary.validity_sample,
            Some(ValiditySampleSummary {
                checked: 2,
                real_disagreements: 0,
                matched_records: 0,
                missing_words: vec![],
            })
        );
    }

    #[test]
    fn sample_parser_accepts_blank_hex_and_decimal_lines() {
        let dir = tempdir().unwrap();
        let path = sample_file(dir.path(), &["", "  0x10  ", "17", ""]);
        assert_eq!(parse_sample_words(&path).unwrap(), vec![16, 17]);
    }

    #[test]
    fn sample_parser_rejects_empty_and_invalid_files() {
        let dir = tempdir().unwrap();
        let empty = sample_file(dir.path(), &["", "  "]);
        assert!(parse_sample_words(&empty)
            .unwrap_err()
            .contains("no sample words"));
        let invalid = sample_file(dir.path(), &["0x100000000"]);
        let error = parse_sample_words(&invalid).unwrap_err();
        assert!(error.contains("invalid sample word"));
        assert!(error.contains("line 1"));
    }

    #[test]
    fn sample_check_rejects_missing_bitmap() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("000.zst"),
            &[record("VALIDITY", "0x00000000")],
        );
        let bitmap_dir = bitmaps(dir.path(), &[(0, [true, false, true, false])]);
        fs::remove_file(bitmap_dir.join("llvm.bin")).unwrap();
        let words = sample_file(dir.path(), &["0"]);

        let error =
            validate_corpus_with_sample(dir.path(), 1, Path::new("zstd"), &bitmap_dir, &words)
                .unwrap_err();
        assert!(error.contains("open bitmap"));
        assert!(error.contains("llvm.bin"));
    }

    #[test]
    fn sample_check_rejects_truncated_bitmap() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("000.zst"),
            &[record("VALIDITY", "0x00000008")],
        );
        let bitmap_dir = bitmaps(dir.path(), &[(8, [true, false, true, false])]);
        fs::write(bitmap_dir.join("spec.bin"), [0_u8]).unwrap();
        let words = sample_file(dir.path(), &["8"]);

        let error =
            validate_corpus_with_sample(dir.path(), 1, Path::new("zstd"), &bitmap_dir, &words)
                .unwrap_err();
        assert!(error.contains("read bitmap"));
        assert!(error.contains("spec.bin"));
    }

    #[test]
    fn schema_only_summary_omits_sample_json() {
        let dir = tempdir().unwrap();
        compressed(
            &dir.path().join("000.zst"),
            &[record("VALIDITY", "0x00000000")],
        );
        let summary = validate_corpus(dir.path(), 1, Path::new("zstd")).unwrap();
        assert_eq!(summary.validity_sample, None);
        let json = serde_json::to_string(&summary).unwrap();
        assert!(!json.contains("validity_sample"));
    }
}
