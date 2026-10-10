//! Local cluster lifecycle. Catalog publication is separate from PostgreSQL durability.
pub mod cache;
pub mod lifecycle;
pub mod objects;
pub mod paired;
pub mod project;
mod retention;
pub mod storage;

pub mod supabase;
pub mod supabase_runtime;
