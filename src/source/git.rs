use super::{Result, Source, Spec};
use crate::lock::{Fetch, Pin};

pub struct Git;

impl Source for Git {
    fn type_name(&self) -> &'static str {
        "git"
    }
    fn kinds(&self) -> &'static [&'static str] {
        &["git"]
    }
    fn prefetch_keys(&self) -> &'static [&'static str] {
        &[]
    }

    fn find(&self, _spec: &Spec) -> Result<Pin> {
        todo!()
    }
    fn prefetch(&self, _pin: &mut Pin) -> Result<()> {
        todo!()
    }
    fn fetch(&self, _pin: &Pin) -> Result<Fetch> {
        todo!()
    }
}
