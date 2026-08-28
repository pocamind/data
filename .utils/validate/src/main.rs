#[macro_use]
mod check;

use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::Ordering;

use anyhow::Result;
use deepwoken::data::DeepData;
use deepwoken::req::{PrereqGroup, Requirement};
use deepwoken::util::graph::PrereqGraph;
use deepwoken::util::name_to_identifier;
use serde_json::Value;

/// Namespaces a prereq may point at. Requirement-only, deliberately narrower than the full
/// id index below.
const NAMESPACES: &[&str] = &[
    "talent", "mantra", "weapon", "outfit", "equipment", "aspect", "origin", "enchant",
    "resonance", "objective",
];
const EXCLUSIVE: &[&str] = &["origin", "outfit", "aspect"];

/// Every bundle category and the namespace its rows are referenced by. A category that is not
/// listed here is an error, so a new table has to be registered before anything can point at it.
const CATEGORY_NAMESPACES: &[(&str, &str)] = &[
    ("aspects", "aspect"),
    ("books", "book"),
    ("dialogs", "dialog"),
    ("enchants", "enchant"),
    ("entities", "entity"),
    ("equipment", "equipment"),
    ("events", "event"),
    ("factions", "faction"),
    ("items", "item"),
    ("locations", "location"),
    ("mantras", "mantra"),
    ("objectives", "objective"),
    ("objects", "object"),
    ("origins", "origin"),
    ("outfits", "outfit"),
    ("potion_effects", "potion_effect"),
    ("presets", "preset"),
    ("resonances", "resonance"),
    ("talents", "talent"),
    ("weapons", "weapon"),
];

/// Fields any lore row may carry. `desc` is the wiki page's own descriptive prose and
/// `additional_info` its Trivia section, each one markdown string transcribed from the page.
/// `wiki` names the page in the wiki mirror a row was written from, which is checked against
/// nothing, as the mirror is not part of this repo.
const COMMON_LORE_FIELDS: &[&str] = &["name", "aliases", "desc", "additional_info", "wiki"];

/// A lore table's closed field set. Any field outside `COMMON_LORE_FIELDS`, `plain` and `refs`
/// is rejected.
struct LoreTable {
    category: &'static str,
    /// Extra fields with no reference semantics.
    plain: &'static [&'static str],
    /// Relation kind, and the namespaces its targets may live in. Values are always arrays of
    /// qualified ids, and the field name is what the relation means.
    refs: &'static [(&'static str, &'static [&'static str])],
}

const LORE_TABLES: &[LoreTable] = &[
    LoreTable {
        category: "entities",
        plain: &["goals"],
        refs: &[
            ("member_of", &["faction"]),
            ("leads", &["faction", "location"]),
            ("found_at", &["location"]),
            ("kin_of", &["entity"]),
            ("wields", &["weapon"]),
            ("holds", &["object"]),
        ],
    },
    LoreTable {
        category: "locations",
        plain: &[],
        refs: &[("part_of", &["location", "faction"])],
    },
    LoreTable {
        category: "factions",
        // `official` absent means true. False marks an informal group rather than an
        // established faction.
        plain: &["official"],
        refs: &[("part_of", &["faction"]), ("holds", &["object"])],
    },
    LoreTable { category: "objects", plain: &[], refs: &[] },
    LoreTable { category: "events", plain: &[], refs: &[] },
];

/// `dialogs` rows share nothing with the other lore tables. All three fields are required.
const DIALOG_FIELDS: &[&str] = &["name", "speaker", "lines"];

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
    let index = IdIndex::build(bundle);

    check_identifiers(bundle);
    check_reqs_bare(bundle);
    check_prereqs(bundle, graph);
    check_exclusive_or_groups(bundle);
    check_cycle_free(graph);
    check_tokens(bundle, &index);
    check_lore_rows(bundle, &index);
    check_dialogs(bundle, &index);
    check_book_authors(bundle, &index);
    check_lore_requirement_firewall(bundle);
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

/// Every `namespace:key` in the bundle, and the namespaces they were collected from.
/// This is what any reference, inline or structured, is resolved against.
struct IdIndex {
    ids: HashSet<String>,
    namespaces: HashSet<&'static str>,
}

impl IdIndex {
    fn build(bundle: &Value) -> IdIndex {
        let mut index = IdIndex { ids: HashSet::new(), namespaces: HashSet::new() };

        for (category, items) in categories(bundle) {
            let Some(namespace) = check!(
                CATEGORY_NAMESPACES
                    .iter()
                    .find(|(name, _)| *name == category.as_str())
                    .map(|(_, namespace)| *namespace),
                "{category}: category has no namespace, register it in CATEGORY_NAMESPACES"
            ) else {
                continue;
            };

            index.namespaces.insert(namespace);
            for key in items.keys() {
                index.ids.insert(format!("{namespace}:{key}"));
            }
        }

        index
    }

    /// Checks a qualified id resolves to a row, and that it lands in one of `allowed` when the
    /// reference site restricts where it may point.
    fn check_id(&self, id: &str, allowed: Option<&[&str]>, context: &str) {
        let Some((namespace, _)) = id.split_once(':') else {
            push_error(&format!("{context}: '{id}' is not qualified with a namespace"));
            return;
        };

        if let Some(allowed) = allowed
            && !allowed.contains(&namespace)
        {
            push_error(&format!(
                "{context}: '{id}' points at namespace '{namespace}', expected one of {allowed:?}"
            ));
            return;
        }

        if !self.namespaces.contains(namespace) {
            push_error(&format!("{context}: '{id}' has unknown namespace '{namespace}'"));
            return;
        }

        check!(self.ids.contains(id), "{context}: '{id}' does not resolve to an existing row");
    }
}

/// Inline references live inside prose as markdown links whose href is a qualified id, for
/// example `[Kennith](entity:kennith)`. The surface text is the game's own wording, so only the
/// href is checked. Grammar: `](namespace:key)`, lowercase, digits allowed in the key. A
/// malformed candidate is surfaced too (`well_formed` false) so the caller can reject a
/// mis-cased or mis-charactered ref instead of letting it pass as plain prose; ordinary
/// links like `](https://…)` also arrive that way and the caller tells them apart by
/// whether the namespace is a known one.
fn scan_tokens(text: &str, mut on_token: impl FnMut(&str, bool)) {
    for (at, _) in text.match_indices("](") {
        let rest = &text[at + 2..];
        let Some(end) = rest.find(')') else {
            continue;
        };

        let token = &rest[..end];
        let Some((namespace, key)) = token.split_once(':') else {
            continue;
        };

        let bare = |c: char| c.is_ascii_lowercase() || c == '_';
        let well_formed = !namespace.is_empty()
            && namespace.chars().all(bare)
            && !key.is_empty()
            && key.chars().all(|c| bare(c) || c.is_ascii_digit());

        on_token(token, well_formed);
    }
}

fn collect_strings<'a>(value: &'a Value, out: &mut Vec<&'a str>) {
    match value {
        Value::String(text) => out.push(text),
        Value::Array(items) => items.iter().for_each(|item| collect_strings(item, out)),
        Value::Object(fields) => fields.values().for_each(|field| collect_strings(field, out)),
        _ => {}
    }
}

/// Tokens may appear in any string of any row, lore or mechanical, so every string in the
/// bundle is scanned.
fn check_tokens(bundle: &Value, index: &IdIndex) {
    for (category, items) in categories(bundle) {
        for (key, entry) in items {
            let mut texts = Vec::new();
            collect_strings(entry, &mut texts);

            for text in texts {
                scan_tokens(text, |token, well_formed| {
                    if well_formed {
                        index.check_id(token, None, &format!("{category}/{key}: token"));
                    } else if let Some((namespace, _)) = token.split_once(':')
                        && index.namespaces.contains(namespace.to_ascii_lowercase().as_str())
                    {
                        push_error(&format!(
                            "{category}/{key}: near-miss token '{token}', bad casing or characters in a known namespace"
                        ));
                    }
                });
            }
        }
    }
}

fn check_lore_rows(bundle: &Value, index: &IdIndex) {
    for table in LORE_TABLES {
        let Some(items) = bundle.get(table.category).and_then(Value::as_object) else {
            continue;
        };

        for (key, entry) in items {
            let context = format!("{}/{key}", table.category);
            let Some(fields) = check!(entry.as_object(), "{context}: row is not an object") else {
                continue;
            };

            for field in fields.keys() {
                let known = COMMON_LORE_FIELDS.contains(&field.as_str())
                    || table.plain.contains(&field.as_str())
                    || table.refs.iter().any(|(name, _)| *name == field.as_str());

                check!(known, "{context}: unknown field '{field}'");
            }

            for field in ["name", "desc", "additional_info", "wiki"] {
                if let Some(value) = fields.get(field) {
                    check!(value.is_string(), "{context}: '{field}' is not a string");
                }
            }

            if let Some(value) = fields.get("official") {
                check!(value.is_boolean(), "{context}: 'official' is not a boolean");
            }

            for field in ["aliases", "goals"] {
                let Some(value) = fields.get(field) else {
                    continue;
                };
                let Some(entries) =
                    check!(value.as_array(), "{context}: '{field}' is not an array of strings")
                else {
                    continue;
                };
                for entry in entries {
                    check!(entry.is_string(), "{context}: '{field}' entry is not a string");
                }
            }

            for (field, allowed) in table.refs {
                let Some(value) = fields.get(*field) else {
                    continue;
                };
                let Some(targets) =
                    check!(value.as_array(), "{context}: '{field}' is not an array of ids")
                else {
                    continue;
                };

                for target in targets {
                    let Some(id) =
                        check!(target.as_str(), "{context}: '{field}' entry is not a string")
                    else {
                        continue;
                    };
                    index.check_id(id, Some(allowed), &format!("{context}: {field}"));
                }
            }
        }
    }
}

fn check_dialogs(bundle: &Value, index: &IdIndex) {
    let Some(items) = bundle.get("dialogs").and_then(Value::as_object) else {
        return;
    };

    for (key, entry) in items {
        let context = format!("dialogs/{key}");
        let Some(fields) = check!(entry.as_object(), "{context}: row is not an object") else {
            continue;
        };

        for field in fields.keys() {
            check!(
                DIALOG_FIELDS.contains(&field.as_str()),
                "{context}: unknown field '{field}'"
            );
        }

        if let Some(speaker) = check!(
            fields.get("speaker").and_then(Value::as_str),
            "{context}: missing 'speaker', a qualified 'entity:' id"
        ) {
            index.check_id(speaker, Some(&["entity"]), &format!("{context}: speaker"));
        }

        let Some(lines) = check!(
            fields.get("lines").and_then(Value::as_array),
            "{context}: missing 'lines', the speaker's own spoken lines"
        ) else {
            continue;
        };

        for line in lines {
            check!(line.as_str(), "{context}: 'lines' entry is not a string");
        }
    }
}

fn check_book_authors(bundle: &Value, index: &IdIndex) {
    let Some(items) = bundle.get("books").and_then(Value::as_object) else {
        return;
    };

    for (key, entry) in items {
        let Some(field) = entry.get("author") else {
            continue;
        };

        let context = format!("books/{key}: author");
        let Some(author) = check!(field.as_str(), "{context}: is not a string") else {
            continue;
        };
        index.check_id(author, Some(&["entity"]), &context);
    }
}

/// `reqs` and `prereqs` belong to the requirement framework. Lore rows are the other half of the
/// data and gate nothing, so carrying either field means the two systems have been mixed up.
fn check_lore_requirement_firewall(bundle: &Value) {
    let lore = LORE_TABLES.iter().map(|table| table.category).chain(["dialogs"]);

    for category in lore {
        let Some(items) = bundle.get(category).and_then(Value::as_object) else {
            continue;
        };

        for (key, entry) in items {
            for field in ["reqs", "prereqs"] {
                check!(
                    entry.get(field).is_none(),
                    "{category}/{key}: lore rows must not carry '{field}'"
                );
            }
        }
    }
}
