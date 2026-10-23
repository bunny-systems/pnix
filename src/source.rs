//! The source-type extension point.
//!
//! A source turns a declaration into a locked node in three steps, and the caller owns
//! everything between them: [`Source::find`] resolves, [`Source::prefetch`] downloads,
//! [`Source::fetch`] derives the discriminator.
//!
//! `type` selects the implementation; `fetch.kind` is what the resolver dispatches on.

use crate::lock::{Fetch, Pin, Rest};
use crate::refs;
use serde::Deserialize;
use serde_json::Value;
use std::collections::BTreeMap;
use std::fmt;

pub mod git;

#[derive(Debug)]
pub enum Error {
    /// The declaration cannot be resolved as written. A missing locator, two revision
    /// strategies at once, a value this type cannot carry.
    ///
    /// The caller adds the pin's name; this message says only what is wrong.
    Declaration(String),
    /// A ref lookup failed.
    Refs(refs::Error),
    /// A node reached [`Source::fetch`] without a field the primitive needs. Reachable from
    /// a hand-edited lock, so not a panic.
    Incomplete {
        kind: &'static str,
        field: &'static str,
    },
    /// Declared, accepted by the schema, not implemented in this build yet. Names where it
    /// is coming from, so it cannot be read as a bug.
    NotYet {
        field: &'static str,
        milestone: &'static str,
    },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Declaration(why) => write!(f, "{why}"),
            Error::Refs(err) => write!(f, "{err}"),
            Error::Incomplete { kind, field } => {
                write!(f, "a {kind} fetch needs `{field}`, and this pin has none")
            }
            Error::NotYet { field, milestone } => {
                write!(f, "`{field}` is not supported yet; it lands in {milestone}")
            }
        }
    }
}

impl std::error::Error for Error {}

impl From<refs::Error> for Error {
    fn from(err: refs::Error) -> Self {
        Error::Refs(err)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

/// One pin as declared in nix, before anything is looked up.
///
/// The field set is closed. An unknown key is a typo. Naming the file it came from is the
/// collector's job, so unknown keys are kept in `rest` rather than refused here.
///
/// Not [`crate::lock::Pin`]. A declaration says what to track; a pin says what was found.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
pub struct Spec {
    // what and where
    #[serde(rename = "type")]
    pub source_type: Option<String>,
    pub source: Option<String>,

    // revision
    #[serde(rename = "ref")]
    pub reference: Option<String>,
    pub rev: Option<String>,
    pub tag: Option<String>,
    pub release: Option<String>,

    // fetchGit switches
    #[serde(rename = "exportIgnore")]
    pub export_ignore: Option<bool>,
    pub submodules: Option<bool>,
    pub shallow: Option<bool>,
    pub lfs: Option<bool>,

    // for evaluation
    #[serde(rename = "excludeFollow")]
    pub exclude_follow: Option<Vec<String>>,
    pub follows: Option<BTreeMap<String, String>>,
    pub flake: Option<bool>,
    pub dir: Option<String>,
    pub importable: Option<bool>,

    // patches
    /// Which pull-request API to talk to, for a type that implies none.
    pub forge: Option<String>,
    /// Patch declarations, carried untouched. Resolved in M6, which is where this stops
    /// being [`Value`].
    pub patches: Option<Vec<Value>>,

    /// Keys this build does not know. Kept so the collector can name them with their file.
    #[serde(flatten)]
    pub rest: Rest,
}

/// How a declaration becomes a locked node.
///
/// Three steps, in order: [`Source::find`], [`Source::prefetch`], [`Source::fetch`]. The
/// caller owns everything between them.
///
/// Implementations are stateless unit structs, reached as `&'static dyn Source`. They cache
/// nothing. The one subprocess seam is [`crate::refs::ls_remote`], and a cache belongs there.
///
/// Two things no implementation may write. `flake`, `dir`, `follows`, `excludeFollow` and
/// `importable` are merged by the caller, between `prefetch` and `fetch`. `rest` belongs to
/// the collector.
///
/// `conformance::check` asserts the rules below. Every implementation's tests run it.
pub trait Source {
    /// The `type` this implementation answers to.
    ///
    /// Three jobs, one string: the registry key, the `type` recorded in the node, and the
    /// name an error message uses. Unique across implementations.
    fn type_name(&self) -> &'static str;

    /// The fetch primitives [`Source::fetch`] may return.
    ///
    /// Every one needs a fetcher in the vendored resolver. A new primitive means a vendored
    /// change, and a `pnix init` in every consumer's repository. A new source type that
    /// reuses an existing primitive costs nobody anything.
    fn kinds(&self) -> &'static [&'static str];

    /// The fields [`Source::prefetch`] produces. Not what it reads.
    ///
    /// Exactly those fields. The caller refetches an entry that is missing one, instead of
    /// reporting it unchanged.
    ///
    /// List too few and a half-written entry freezes. Measured: a pin with a `hash` and no
    /// `lastModified` kept building as `...19700101.<rev>` while `update` said "unchanged".
    ///
    /// List too many and every run warns about a field that was never coming.
    fn prefetch_keys(&self) -> &'static [&'static str];

    /// Resolves a declaration into a locked node.
    ///
    /// Asks the remote what exists. Never retrieves the source itself — that is
    /// [`Source::prefetch`]'s job, and `pnix look` depends on the split.
    ///
    /// The node carries `type` from [`Source::type_name`], plus every field that decides
    /// what will be fetched. The caller compares it against the previous entry before
    /// downloading anything.
    ///
    /// `hash`, `lastModified` and `fetch` stay unset. Filling them here would skip the
    /// checks that catch an incomplete entry.
    fn find(&self, spec: &Spec) -> Result<Pin>;

    /// Records what the bytes are: `hash`, and `lastModified` where the download reveals it.
    ///
    /// Adds only. `source`, `rev`, `ref` and `tag` must come out unchanged, because the
    /// caller has already compared them.
    ///
    /// Idempotent. Twice over one node gives the same fields.
    ///
    /// Nothing to add is a valid answer. A git rev already commits to the tree, so
    /// [`Source::prefetch_keys`] is empty there and this is a no-op.
    ///
    /// `Ok(())` with a declared prefetch key still unset is a defect. The caller reports it
    /// instead of writing an incomplete entry.
    fn prefetch(&self, pin: &mut Pin) -> Result<()>;

    /// Builds the `fetch` discriminator the vendored resolver reads.
    ///
    /// Nix builds the derivation from this at eval time. The resolver dispatches on `kind` and never learns this source exists.
    ///
    /// Adds no integrity of its own. The hash arrived with [`Source::prefetch`]; this
    /// selects and renames fields the node already has.
    ///
    /// Pure and deterministic. No network, no subprocess. One node must give one
    /// discriminator on every run, or the lock is not stable.
    ///
    /// Called last, after the caller has merged the fields it owns.
    ///
    /// A field the primitive needs and the node lacks is [`Error::Incomplete`].
    fn fetch(&self, pin: &Pin) -> Result<Fetch>;
}

/// Every source this build has. A new one is one line here, and the registry, the error
/// messages and the conformance sweep all pick it up.
pub const ALL: &[&dyn Source] = &[&git::Git];

/// The implementation for a declared `type`, or `None` if nothing answers to it.
pub fn source_for(type_name: &str) -> Option<&'static dyn Source> {
    ALL.iter().copied().find(|src| src.type_name() == type_name)
}

/// The rules in [`Source`], as assertions. Every implementation's tests run [`check`].
#[cfg(test)]
pub mod conformance {
    use super::*;

    /// Primitives the vendored resolver has a fetcher for. [`Fetch`] is the authority; this
    /// list exists so a typo in `kinds()` is caught here rather than at eval time.
    const PRIMITIVES: &[&str] = &["tarball", "file", "git", "path"];

    /// Fields a source may add in [`Source::prefetch`], and nothing else.
    const PREFETCHABLE: &[&str] = &["hash", "lastModified"];

    /// `kind` as it will be written, read back through serde rather than guessed.
    fn kind_of(fetch: &Fetch) -> String {
        serde_json::to_value(fetch).expect("a Fetch serializes")["kind"]
            .as_str()
            .expect("kind is a string")
            .to_owned()
    }

    fn carried(pin: &Pin) -> Vec<&'static str> {
        let mut out = Vec::new();
        if pin.flake.is_some() {
            out.push("flake");
        }
        if pin.dir.is_some() {
            out.push("dir");
        }
        if pin.follows.is_some() {
            out.push("follows");
        }
        if pin.exclude_follow.is_some() {
            out.push("excludeFollow");
        }
        if pin.importable.is_some() {
            out.push("importable");
        }
        out
    }

    /// What the caller has already compared, and what [`Source::prefetch`] must therefore
    /// leave alone. Owned, so the snapshot outlives the borrow.
    ///
    /// A pin's locator is `url`, `path` or `channel` — `source` is the *declaration's* field,
    /// and D14 did not touch the lock.
    fn identity(pin: &Pin) -> Vec<Option<String>> {
        vec![
            pin.url.clone(),
            pin.path.clone(),
            pin.channel.clone(),
            pin.rev.clone(),
            pin.reference.clone(),
            pin.tag.clone(),
        ]
    }

    fn produced(pin: &Pin, key: &str) -> bool {
        match key {
            "hash" => pin.hash.is_some(),
            "lastModified" => pin.last_modified.is_some(),
            other => panic!("prefetch_keys names `{other}`, which no source may produce"),
        }
    }

    /// The rules that need no network: the type name, the kinds, the prefetch keys.
    ///
    /// Cheap enough to run over every entry in [`ALL`], so a source is partly gated from the
    /// moment it is registered.
    pub fn check_static(src: &dyn Source) {
        let name = src.type_name();
        assert!(
            !name.is_empty() && name.chars().all(|c| c.is_ascii_lowercase() || c == '-'),
            "{name:?} is not usable as a nix `type` value"
        );

        assert!(!src.kinds().is_empty(), "{name}: kinds() is empty");
        for kind in src.kinds() {
            assert!(
                PRIMITIVES.contains(kind),
                "{name}: kinds() names {kind:?}, which the resolver has no fetcher for"
            );
        }
        for key in src.prefetch_keys() {
            assert!(
                PREFETCHABLE.contains(key),
                "{name}: prefetch_keys() names {key:?}, which prefetch may not produce"
            );
        }
    }

    /// [`check_static`], then all three steps over `spec`.
    ///
    /// `spec` must be one this source can really resolve: everything after [`Source::find`]
    /// needs a real node.
    pub fn check(src: &dyn Source, spec: &Spec) {
        check_static(src);
        let name = src.type_name();

        let mut pin = src.find(spec).expect("find resolves the spec");
        assert_eq!(
            pin.source_type.as_deref(),
            Some(name),
            "{name}: find did not stamp its own type"
        );
        assert_eq!(
            carried(&pin),
            Vec::<&str>::new(),
            "{name}: find wrote a carried field"
        );
        assert!(pin.rest.is_empty(), "{name}: find wrote to rest");
        assert!(pin.hash.is_none(), "{name}: find set a hash");
        assert!(pin.last_modified.is_none(), "{name}: find set lastModified");
        assert!(pin.fetch.is_none(), "{name}: find set fetch");

        let before = identity(&pin);
        src.prefetch(&mut pin).expect("prefetch succeeds");
        assert_eq!(
            identity(&pin),
            before,
            "{name}: prefetch changed an identity field"
        );
        for key in src.prefetch_keys() {
            assert!(produced(&pin, key), "{name}: prefetch left {key:?} unset");
        }

        let once = pin.clone();
        src.prefetch(&mut pin).expect("prefetch is idempotent");
        assert_eq!(pin, once, "{name}: prefetch is not idempotent");

        let fetch = src.fetch(&pin).expect("fetch builds a discriminator");
        assert!(
            src.kinds().contains(&kind_of(&fetch).as_str()),
            "{name}: fetch returned a kind that kinds() does not list"
        );
        assert_eq!(
            src.fetch(&pin).expect("fetch is deterministic"),
            fetch,
            "{name}: fetch is not deterministic"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Both spellings of every renamed field, in the form a declaration really uses.
    ///
    /// A rename that drifts does not fail to compile — the key stops matching and the
    /// value is silently lost — so this test is the only thing holding the wire names.
    #[test]
    fn the_nix_spellings_land_on_the_rust_fields() {
        let spec: Spec = serde_json::from_str(
            r#"{ "type": "git", "ref": "main", "exportIgnore": true,
                 "excludeFollow": ["nixpkgs"] }"#,
        )
        .expect("parses");

        assert_eq!(spec.source_type.as_deref(), Some("git"));
        assert_eq!(spec.reference.as_deref(), Some("main"));
        assert_eq!(spec.export_ignore, Some(true));
        assert_eq!(
            spec.exclude_follow.as_deref(),
            Some(["nixpkgs".to_owned()].as_slice())
        );
        assert!(
            spec.rest.is_empty(),
            "a known key was left in rest: {:?}",
            spec.rest
        );
    }

    /// Every field at once, so nothing is unreachable from a declaration.
    #[test]
    fn a_declaration_using_the_whole_surface_parses() {
        let spec: Spec = serde_json::from_str(
            r#"{
                 "type": "git",
                 "source": "ssh://git@github.com/nimeses/nix-assets.git",
                 "ref": "main",
                 "rev": "288a13dacbebf7d871faaecbebd84ae9ad45d04b",
                 "tag": "v1.1.0",
                 "release": "^1.3",
                 "submodules": true, "shallow": true, "lfs": true, "exportIgnore": true,
                 "follows": { "nixpkgs": "nixpkgs" },
                 "excludeFollow": ["hjem"],
                 "flake": false,
                 "dir": "pkgs/firefox-addons",
                 "importable": true,
                 "forge": "forgejo",
                 "patches": [ { "pr": 181 }, "./patches/local.patch" ]
               }"#,
        )
        .expect("parses");

        assert_eq!(
            spec.source.as_deref(),
            Some("ssh://git@github.com/nimeses/nix-assets.git")
        );
        assert_eq!(
            spec.rev.as_deref(),
            Some("288a13dacbebf7d871faaecbebd84ae9ad45d04b")
        );
        assert_eq!(spec.tag.as_deref(), Some("v1.1.0"));
        assert_eq!(spec.release.as_deref(), Some("^1.3"));
        assert_eq!(
            (spec.submodules, spec.shallow, spec.lfs, spec.export_ignore),
            (Some(true), Some(true), Some(true), Some(true))
        );
        assert_eq!(spec.follows.expect("follows")["nixpkgs"], "nixpkgs");
        assert_eq!(spec.flake, Some(false));
        assert_eq!(spec.dir.as_deref(), Some("pkgs/firefox-addons"));
        assert_eq!(spec.importable, Some(true));
        assert_eq!(spec.forge.as_deref(), Some("forgejo"));
        assert_eq!(spec.patches.expect("patches").len(), 2);
        assert!(
            spec.rest.is_empty(),
            "nothing should be left over: {:?}",
            spec.rest
        );
    }

    /// A key this build does not know is kept rather than dropped, so the collector can
    /// report it together with the file it came from.
    #[test]
    fn an_unknown_key_is_kept_for_the_collector_to_report() {
        let spec: Spec =
            serde_json::from_str(r#"{ "type": "github", "tagsFrom": "x" }"#).expect("parses");

        assert_eq!(spec.rest.get("tagsFrom").and_then(Value::as_str), Some("x"));
    }

    /// Which is also what catches a misspelled *known* key: it lands in `rest` instead of
    /// quietly doing nothing, and the collector refuses it.
    #[test]
    fn a_misspelled_known_key_is_not_silently_dropped() {
        let spec: Spec =
            serde_json::from_str(r#"{ "excludeFollows": ["nixpkgs"] }"#).expect("parses");

        assert_eq!(spec.exclude_follow, None);
        assert!(
            spec.rest.contains_key("excludeFollows"),
            "rest: {:?}",
            spec.rest
        );
    }

    /// An empty declaration declares nothing: no field may acquire a value on its own,
    /// because every default here is a policy decision made elsewhere (`ref` defaulting
    /// to HEAD, `flake` to true) and a `Spec` must not pre-empt it.
    #[test]
    fn an_empty_declaration_is_all_none() {
        let spec: Spec = serde_json::from_str("{}").expect("parses");
        assert_eq!(spec, Spec::default());
    }

    /// A `type` resolves to the source that answers to it, for every source there is.
    ///
    /// The declared string and [`Source::type_name`] are the same string by construction,
    /// because the registry searches on it instead of repeating it in a `match`.
    #[test]
    fn every_source_is_reachable_by_its_own_type_name() {
        for src in ALL {
            let found = source_for(src.type_name())
                .unwrap_or_else(|| panic!("{} is in ALL but not findable", src.type_name()));
            assert_eq!(found.type_name(), src.type_name());
        }
    }

    /// Two sources answering to one `type` would make the lookup order decide which one
    /// resolves a pin, and `ALL`'s order is nothing but how it was typed.
    #[test]
    fn no_two_sources_claim_the_same_type_name() {
        let mut seen = std::collections::BTreeSet::new();
        for src in ALL {
            assert!(
                seen.insert(src.type_name()),
                "{} is registered twice",
                src.type_name()
            );
        }
        assert_eq!(seen.len(), ALL.len());
    }

    /// An unknown `type` is `None`, not a guess. The caller turns it into an error naming
    /// what is known.
    #[test]
    fn an_unknown_type_resolves_to_nothing() {
        assert!(source_for("definitely-not-a-source").is_none());
        assert!(source_for("").is_none());
        assert!(source_for("GIT").is_none(), "the lookup is case-sensitive");
    }

    /// Every registered source passes the rules that need no network. This is the port's
    /// `test_every_source_kind_has_a_fetcher`: a source whose `kinds()` names a primitive
    /// the vendored resolver cannot fetch fails here, not at eval time in someone's repo.
    #[test]
    fn every_registered_source_is_statically_conformant() {
        for src in ALL {
            conformance::check_static(*src);
        }
    }

    /// The common shape: two fields set, sixteen untouched.
    #[test]
    fn a_minimal_declaration_leaves_the_rest_unset() {
        let spec: Spec = serde_json::from_str(
            r#"{ "type": "github", "source": "https://github.com/nix-community/disko" }"#,
        )
        .expect("parses");

        assert_eq!(
            spec,
            Spec {
                source_type: Some("github".to_owned()),
                source: Some("https://github.com/nix-community/disko".to_owned()),
                ..Spec::default()
            }
        );
    }
}
