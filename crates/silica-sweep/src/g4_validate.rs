use std::collections::BTreeSet;
use std::fs;
use std::io::{BufRead, BufReader};
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
}

#[derive(Clone)]
struct FileResult {
    records: u64,
    problem: Option<CorpusProblem>,
}

fn problem(path: &Path, line: u64, reason: impl Into<String>) -> FileResult {
    FileResult {
        records: line.saturating_sub(1),
        problem: Some(CorpusProblem {
            file: path.display().to_string(),
            line,
            reason: reason.into(),
        }),
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

fn validate_file(path: &Path, zstd: &Path) -> FileResult {
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
        records += 1;
    }
    drop(reader);

    match child.wait() {
        Ok(status) if status.success() => FileResult {
            records,
            problem: None,
        },
        Ok(status) => problem(path, records + 1, format!("zstd exited with {status}")),
        Err(error) => problem(
            path,
            records + 1,
            format!("could not wait for zstd: {error}"),
        ),
    }
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

    thread::scope(|scope| {
        for _ in 0..workers {
            scope.spawn(|| loop {
                let index = next.fetch_add(1, Ordering::Relaxed);
                let Some(path) = files.get(index) else {
                    break;
                };
                let result = validate_file(path, zstd);
                results.lock().expect("result mutex poisoned")[index] = Some(result);
            });
        }
    });

    let results = results
        .into_inner()
        .map_err(|_| "result mutex poisoned".to_owned())?;
    let mut records_read = 0_u64;
    let mut first_problem = None;
    for result in results {
        let result = result.ok_or_else(|| "worker returned no result".to_owned())?;
        records_read += result.records;
        if first_problem.is_none() {
            first_problem = result.problem;
        }
    }
    Ok(CorpusSummary {
        files: files.len(),
        records_read,
        workers,
        problem: first_problem,
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
}
