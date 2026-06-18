use std::fs::{self, OpenOptions};
use std::io::Write;
use std::sync::atomic::{AtomicUsize, Ordering};

use crate::error_file;

pub static ERROR_COUNT: AtomicUsize = AtomicUsize::new(0);

pub fn push_error(msg: &str) {
    ERROR_COUNT.fetch_add(1, Ordering::Relaxed);

    let path = error_file();
    fs::create_dir_all(path.parent().unwrap()).ok();

    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .expect("failed to open error file");

    writeln!(f, "{msg}").expect("failed to write to error file");
    eprintln!("{msg}");
}

/// Soft assertion that returns an `Option<T>`.
/// On success, yields `Some(value)`. On failure, logs the error and yields `None`.
macro_rules! check {
    ($expr:expr, $($arg:tt)+) => {
        match $crate::check::Checkable::check($expr) {
            Ok(val) => Some(val),
            Err(e) => {
                $crate::check::push_error(&format!("{}: {e}", format_args!($($arg)+)));
                None
            }
        }
    };
}

/// Make check! work on Option<T>, Result<T, E>, and bool
pub trait Checkable {
    type Value;
    fn check(self) -> Result<Self::Value, String>;
}

impl<T> Checkable for Option<T> {
    type Value = T;
    fn check(self) -> Result<T, String> {
        self.ok_or_else(|| "got None".into())
    }
}

impl<T, E: std::fmt::Display> Checkable for std::result::Result<T, E> {
    type Value = T;
    fn check(self) -> Result<T, String> {
        self.map_err(|e| e.to_string())
    }
}

impl Checkable for bool {
    type Value = ();
    fn check(self) -> Result<(), String> {
        if self { Ok(()) } else { Err("condition was false".into()) }
    }
}
