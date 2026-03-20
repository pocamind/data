use deepwoken::req::Requirement;
use serde_json::Value;

use crate::ctx::TransformContext;


impl TransformContext {
    /// Replace all instances of a prereq name with another.
    /// Set `to`: None to remove the prereq.
    pub fn replace_prereqs(&mut self, from: &str, to: Option<&str>) {
        for (_, rows) in &mut self.tables {
            for (_, value) in rows {
                let req_field = if let Some(field) = value.get_mut("reqs") {
                    field
                } else { continue };

                let req_str = req_field.as_str().expect("requirement field was not a string type"); 
                let mut req = Requirement::parse(req_str).unwrap();
                
                req.prereqs.remove(from);
                
                if let Some(to) = to {
                    req.prereqs.insert(to.into());
                }

                *req_field = Value::String(req.to_string());
            }
        }
    }
}