use std::{collections::HashMap, path::PathBuf};

use serde_json::Value;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// A TransformationContext object provides utilities for transforming the data present
/// at the repo's root (in parallel if applicable), and writing back the result. 
pub struct TransformContext {
    tables: HashMap<
        String,
        // this is a map of values corresponding to each 'name' field -> a UID
        // (which is the id of the row semantically, which is also the name of the file)
        HashMap<String, Value>
    >
}

impl TransformContext {
    /// creates a new transformation context, using data at the root of the repository
    pub async fn new() -> anyhow::Result<Self> {
        let dir = repo_root().read_dir()?;
        for entry in dir {
            let entry = entry?;
        }
        todo!()
    }
}