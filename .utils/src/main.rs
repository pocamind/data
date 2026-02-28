#[macro_use]
mod check;

use std::fs;
use std::path::PathBuf;
use std::sync::atomic::Ordering;

use anyhow::Result;
use deepwoken::data::DeepData;
use deepwoken::util::name_to_identifier;
use serde_json::Value;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn error_file() -> PathBuf {
    repo_root().join(".tmp").join("errors.txt")
}

fn read_bundle(name: &str) -> Result<Value> {
    let path = repo_root().join(".dist").join(format!("{name}.json"));
    let content = fs::read_to_string(&path)?;
    Ok(serde_json::from_str(&content)?)
}

fn main() {
    // Clear previous run
    let path = error_file();
    if path.exists() {
        fs::remove_file(&path).ok();
    }

    let bundle = read_bundle("all").expect("failed to read all.json");

    validate(&bundle);

    let errors = check::ERROR_COUNT.load(Ordering::Relaxed);
    if errors > 0 {
        eprintln!("\n{errors} validation error(s) found. See .tmp/errors.txt");
        std::process::exit(1);
    }

    println!("all checks passed");
}

fn validate(bundle: &Value) {
    check_identifiers(bundle);
    check_parsable(bundle);
}

/// Any entry's key must equal name_to_identifier(entry["name"])
fn check_identifiers(bundle: &Value) {
    let Some(categories) = check!(bundle.as_object(), "bundle should be an object") else { return };

    for (category, items) in categories {
        let Some(items) = check!(items.as_object(), "{category}: should be an object") else { continue };

        for (key, entry) in items {
            let Some(name) = check!(
                entry.get("name").and_then(Value::as_str),
                "{category}/{key}: missing 'name' field"
            ) else { continue };

            let expected = name_to_identifier(name).to_lowercase();
            check!(
                key == &expected,
                "{category}/{key}: identifier mismatch, name '{name}' produces '{expected}'"
            );
        }
    }
}

/// The entire bundle must be parsable into DeepData
fn check_parsable(bundle: &Value) {
    let json = serde_json::to_string(bundle).expect("failed to re-serialize bundle");
    check!(DeepData::from_json(&json), "bundle is not parsable into DeepData");
}
