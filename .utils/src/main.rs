use anyhow::Result;
use deepwoken_reqparse::{util::reqtree::ReqTree};

fn main() {
    println!("Hello, world!");
}

/* A crate to test the data is well formed */

// Contruct a new requirement tree from all named objects in the data
fn get_tree() -> Result<ReqTree> {
    todo!()
}

#[test]
fn all_prereqs_exist() {
    let mut tree = get_tree().unwrap();

    todo!()
}