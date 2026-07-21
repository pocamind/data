#[macro_use]
mod check;

use std::fs;
use std::path::PathBuf;
use std::sync::atomic::Ordering;

use anyhow::Result;
use deepwoken::data::DeepData;
use deepwoken::req::{PrereqGroup, Requirement};
use deepwoken::util::graph::PrereqGraph;
use deepwoken::util::name_to_identifier;
use serde_json::Value;

const NAMESPACES: &[&str] = &[
    "talent", "mantra", "weapon", "outfit", "equipment", "aspect", "origin", "enchant",
    "resonance", "objective",
];
const EXCLUSIVE: &[&str] = &["origin", "outfit", "aspect"];

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
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
    let path = error_file();
    if path.exists() {
        fs::remove_file(&path).ok();
    }

    let bundle = read_bundle("all").expect("failed to read all.json");
    let data = DeepData::from_json(&bundle.to_string()).expect("bundle is not parsable into DeepData");
    let graph = data.prereq_graph();

    validate(&bundle, &graph);

    let errors = check::ERROR_COUNT.load(Ordering::Relaxed);
    if errors > 0 {
        eprintln!("\n{errors} validation error(s) found. See .tmp/errors.txt");
        std::process::exit(1);
    }

    println!("all checks passed");
}

fn validate(bundle: &Value, graph: &PrereqGraph) {
    check_identifiers(bundle);
    check_reqs_bare(bundle);
    check_prereqs(bundle, graph);
    check_exclusive_or_groups(bundle);
    check_cycle_free(graph);
}

fn categories(bundle: &Value) -> Vec<(&String, &serde_json::Map<String, Value>)> {
    let Some(object) = bundle.as_object() else {
        push_error("bundle should be an object");
        return Vec::new();
    };
    object
        .iter()
        .filter_map(|(category, items)| items.as_object().map(|items| (category, items)))
        .collect()
}

fn push_error(msg: &str) {
    check::push_error(msg);
}

fn check_identifiers(bundle: &Value) {
    for (category, items) in categories(bundle) {
        for (key, entry) in items {
            let Some(name) = check!(
                entry.get("name").and_then(Value::as_str),
                "{category}/{key}: missing 'name' field"
            ) else {
                continue;
            };

            let expected = name_to_identifier(name).to_lowercase();
            check!(
                key == &expected,
                "{category}/{key}: identifier mismatch, name '{name}' produces '{expected}'"
            );
        }
    }
}

fn check_reqs_bare(bundle: &Value) {
    for (category, items) in categories(bundle) {
        for (key, entry) in items {
            let Some(req_field) = entry.get("reqs") else {
                continue;
            };
            let Some(req_str) = check!(
                req_field.as_str(),
                "{category}/{key}: 'reqs' field is not a string"
            ) else {
                continue;
            };

            let Some(req) = check!(
                Requirement::parse(req_str),
                "{category}/{key}: '{req_str}' is not a valid requirement"
            ) else {
                continue;
            };

            check!(
                req.name.is_none(),
                "{category}/{key}: 'reqs' carries a name prefix, expected stat clauses only"
            );
            check!(
                req.prereqs.is_empty(),
                "{category}/{key}: 'reqs' carries a prereq prefix, expected stat clauses only"
            );
        }
    }
}

fn check_prereqs(bundle: &Value, graph: &PrereqGraph) {
    for (category, items) in categories(bundle) {
        for (key, entry) in items {
            let Some(field) = entry.get("prereqs") else {
                continue;
            };
            let Some(groups) = check!(
                field.as_array(),
                "{category}/{key}: 'prereqs' field is not an array"
            ) else {
                continue;
            };

            for group in groups {
                let Some(group_str) = check!(
                    group.as_str(),
                    "{category}/{key}: 'prereqs' entry is not a string"
                ) else {
                    continue;
                };

                let Some(parsed) = check!(
                    PrereqGroup::parse(group_str),
                    "{category}/{key}: '{group_str}' is not a valid prereq group"
                ) else {
                    continue;
                };

                for alternative in parsed.alternatives() {
                    let Some((namespace, _)) = alternative.split_once(':') else {
                        push_error(&format!(
                            "{category}/{key}: prereq '{alternative}' is not qualified with a namespace"
                        ));
                        continue;
                    };

                    check!(
                        NAMESPACES.contains(&namespace),
                        "{category}/{key}: prereq '{alternative}' has unknown namespace '{namespace}'"
                    );
                    check!(
                        graph.contains(alternative),
                        "{category}/{key}: prereq '{alternative}' does not resolve to an existing row"
                    );
                }
            }
        }
    }
}

fn check_exclusive_or_groups(bundle: &Value) {
    for (category, items) in categories(bundle) {
        for (key, entry) in items {
            let Some(groups) = entry.get("prereqs").and_then(Value::as_array) else {
                continue;
            };

            for exclusive in EXCLUSIVE {
                let count = groups
                    .iter()
                    .filter_map(Value::as_str)
                    .filter(|group| !group.contains('|'))
                    .filter(|group| group.trim().starts_with(&format!("{exclusive}:")))
                    .count();

                check!(
                    count < 2,
                    "{category}/{key}: {count} separate '{exclusive}' prereqs, expected a single OR-group"
                );
            }
        }
    }
}

fn check_cycle_free(graph: &PrereqGraph) {
    if let Some(cycle) = graph.find_cycle() {
        push_error(&format!("prereq graph has a cycle: {}", cycle.join(" -> ")));
    }
}
