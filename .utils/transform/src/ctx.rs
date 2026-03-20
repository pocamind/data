use std::{collections::HashMap, fs::{self, FileType}, path::PathBuf, sync::{Arc, Mutex}};

use rayon::iter::{IntoParallelRefIterator, ParallelIterator};
use serde_json::Value;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// A TransformationContext object provides utilities for transforming the data present
/// at the repo's root (in parallel if applicable), and writing back the result. 
pub struct TransformContext {
    pub(crate) tables: HashMap<
        String,
        // this is a map of values corresponding to each 'name' field -> a UID
        // (which is the id of the row semantically, which is also the name of the file)
        HashMap<String, Value>
    >
}

impl TransformContext {
    /// creates a new transformation context, using data at the root of the repository
    pub fn new() -> anyhow::Result<Self> {
        let dir = repo_root().read_dir()?;
        let mut paths = vec![];

        for entry in dir {
            let entry = entry?;
            if entry.file_type()?.is_dir() 
            // check if not hidden dir
            && !entry.file_name().as_os_str().to_str().unwrap().starts_with(".") {
                // push each json path within this dir
                let table_dir =entry.path();

                for entry in table_dir.read_dir()? {
                    let entry = entry?;
                    if entry.file_type()?.is_file()
                    && entry.path().extension().expect("why is there no file extension") == "json" {
                        paths.push(entry.path());
                    }
                }
            }
        }

        let value_map = Arc::new(Mutex::new(
            HashMap::<String, HashMap<String, Value>>::new()
        ));

        paths.par_iter().for_each(|path| {
            let contents: Value = serde_json::from_str(
                &fs::read_to_string(path).unwrap()
            ).unwrap();

            // average os specific rust code
            let table = path.parent().unwrap()
                .file_name().unwrap()
                .to_str().unwrap()
                .to_string();

            let id = path.as_path().file_stem().unwrap()
                .to_str().unwrap()
                .to_string();
            
            let mut map = value_map.lock().unwrap();
            map.entry(table).or_default()
                .insert(id, contents);
        });

        let tables =  Arc::into_inner(value_map).unwrap().into_inner().unwrap();

        println!("{tables:?}");
        
        Ok(Self {
            tables
        })
    }

    pub fn write_back(self) -> anyhow::Result<()> {
        let mut paths = HashMap::<PathBuf, Value>::new();

        for (table, rows) in self.tables {
            for (id, value) in rows {
                // reconstruct path 
                let mut path = repo_root();
                path.push(table.clone());
                path.push(format!("{id}.json"));

                paths.insert(path, value);
            }
        }

        println!("{paths:?}");

        paths.par_iter().for_each(|(path, val)| {
            fs::write(path, serde_json::to_string_pretty(val).unwrap()).unwrap();
            println!("wrote: {path:?}");
        });

        Ok(())
    }
}