//! Read and write `.pnix/pins.lock.json`.
//!
//! The lock is the only file pnix writes, besides the resolver it vendors into the
//! consumer's repository. Entry points: [`Lock::read`], [`Lock::write`].
//!
//! **Field declaration order is part of the file format.** serde serializes struct fields
//! in declaration order, and the tests require the output to match, byte for byte, the
//! locks the python implementation wrote. Inserting or moving a field rewrites every node
//! of every consumer's lock on its next update; append instead, and if the order has to
//! change, regenerate `tests/fixtures/*.lock.json` deliberately.

use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::io::Write as _;
use std::path::{Path, PathBuf};

use serde::de::Error as _;
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

/// Lock schema this build reads and writes. Any other is refused by [`Lock::parse`].
pub const SCHEMA: u32 = 5;

/// Keys no field here names, kept verbatim so a rewrite never drops them.
///
/// A pin's provenance fields may grow without a schema bump, so a lock written by a newer
/// pnix at this same schema can carry fields this build has never seen.
pub type Rest = BTreeMap<String, Value>;

/// Why a lock could not be read or written.
#[derive(Debug)]
pub enum Error {
    /// Reading, writing, renaming, or creating the lock's directory failed.
    Io { path: PathBuf, err: std::io::Error },
    /// Not JSON, or a field has the wrong type or an unusable value.
    Parse {
        path: PathBuf,
        err: serde_json::Error,
    },
    /// `schema` is missing or is not [`SCHEMA`]. Holds the full message, which names the
    /// file and what to do about it.
    Schema(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Io { path, err } => write!(f, "{}: {err}", path.display()),
            Error::Parse { path, err } => write!(f, "{}: {err}", path.display()),
            Error::Schema(msg) => f.write_str(msg),
        }
    }
}

impl std::error::Error for Error {}

/// [`std::result::Result`] with this module's [`Error`].
pub type Result<T> = std::result::Result<T, Error>;

/// A `sha256-<base64>` SRI hash.
///
/// Deserializing rejects every other algorithm. nix's fetch builtins accept none of them
/// for a pin, so a lock recording one could not be fetched at all.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct Hash(String);

impl Hash {
    /// Check an SRI string. `Err` is a message naming the offending value, suitable for a
    /// user and for [`serde::de::Error::custom`].
    pub fn parse(text: &str) -> std::result::Result<Self, String> {
        match text.split_once('-') {
            Some(("sha256", body)) if !body.is_empty() => Ok(Hash(text.to_owned())),
            Some((algo, _)) => Err(format!(
                "hash {text:?}: nix takes sha256 only for a pin, not {algo:?}"
            )),
            _ => Err(format!(
                "hash {text:?}: not SRI, expected `sha256-<base64>`"
            )),
        }
    }

    /// The SRI string, prefix included.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for Hash {
    fn deserialize<D: Deserializer<'de>>(de: D) -> std::result::Result<Self, D::Error> {
        let text = String::deserialize(de)?;
        Hash::parse(&text).map_err(D::Error::custom)
    }
}

impl fmt::Display for Hash {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A pin's `fetch` node: the arguments the vendored resolver hands a nix fetch builtin.
///
/// Keyed on `kind`, one variant per fetch primitive. A source type (`github`, `forgejo`,
/// `channel`, …) is a pnix-side concept that reduces to one of these, which is why adding
/// a forge changes no vendored nix and costs no consumer a `pnix init`.
///
/// Every field but `kind` and `hash` is passed to the builtin unchanged, so `rest` carries
/// options a newer pnix recorded and this build does not know.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Fetch {
    /// `builtins.fetchTarball`. `hash` is the NAR hash of the unpacked tree.
    Tarball {
        url: String,
        hash: Hash,
        #[serde(flatten)]
        rest: Rest,
    },
    /// `builtins.fetchurl`. `hash` is the flat hash of the bytes, not a NAR hash.
    File {
        url: String,
        hash: Hash,
        #[serde(flatten)]
        rest: Rest,
    },
    /// `builtins.fetchGit`. No hash: the rev commits to the tree.
    ///
    /// `reference` is what lets fetchGit locate `rev` without `allRefs = true`, which
    /// refetches every ref on the remote on every evaluation.
    Git {
        url: String,
        rev: String,
        #[serde(rename = "ref", skip_serializing_if = "Option::is_none", default)]
        reference: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none", default)]
        submodules: Option<bool>,
        #[serde(skip_serializing_if = "Option::is_none", default)]
        shallow: Option<bool>,
        #[serde(skip_serializing_if = "Option::is_none", default)]
        lfs: Option<bool>,
        #[serde(
            rename = "exportIgnore",
            skip_serializing_if = "Option::is_none",
            default
        )]
        export_ignore: Option<bool>,
        #[serde(flatten)]
        rest: Rest,
    },
    /// A directory on the evaluating machine. Nothing is fetched and nothing is checked.
    Path {
        path: String,
        #[serde(flatten)]
        rest: Rest,
    },
}

/// One entry of a pin's `patches`, applied to its fetched tree in order.
///
/// The resolver uses `url` and `hash`; every other field is provenance for `pnix look`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Patch {
    /// A patch file in the consumer's repository, `path` relative to the lock's directory.
    ///
    /// `hash` is the file's own flat hash, so an edit to it is detectable without applying
    /// the patch.
    Path {
        path: String,
        hash: Hash,
        #[serde(flatten)]
        rest: Rest,
    },
    /// A forge pull request's diff.
    ///
    /// `head`, `base`, `state` and `merged` are what `pnix look` reports drift from.
    /// `immutable` is false when the forge serves the diff from a url that moves with the
    /// pull request, in which case `hash` is the only thing holding the content still.
    /// `merge_base` is the tree the forge rendered the diff against, absent when the forge
    /// has no endpoint for it.
    Pr {
        forge: String,
        host: String,
        owner: String,
        repo: String,
        number: u64,
        head: String,
        base: String,
        state: String,
        merged: bool,
        immutable: bool,
        #[serde(rename = "mergeBase", skip_serializing_if = "Option::is_none", default)]
        merge_base: Option<String>,
        url: String,
        hash: Hash,
        #[serde(flatten)]
        rest: Rest,
    },
    /// A single commit's diff, from the forge that hosts it.
    Commit {
        forge: String,
        host: String,
        owner: String,
        repo: String,
        rev: String,
        immutable: bool,
        url: String,
        hash: Hash,
        #[serde(flatten)]
        rest: Rest,
    },
    /// Any url that serves a diff.
    Url {
        immutable: bool,
        url: String,
        hash: Hash,
        #[serde(flatten)]
        rest: Rest,
    },
}

/// One entry of `pins`.
///
/// Every field is optional: the shape depends on the source type, and an entry exists
/// between being resolved and being prefetched, when `hash` and `fetch` are not there yet.
///
/// Two groups, which differ in what changing them costs:
///
/// * **read by the vendored resolver** — `fetch`, `rev`, `hash`, `last_modified`, `flake`,
///   `dir`, `follows`, `exclude_follow`, `patches`, `patched_hash`. Changing what one of
///   these means is a schema bump, and every consumer re-runs `pnix init`.
/// * **provenance, read only by pnix** — everything else. Free to change, because nothing
///   in a consumer's repository looks at it.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Pin {
    /// Source type that produced this entry (`github`, `git`, `channel`, …). Not a fetch
    /// primitive: see [`Fetch`].
    #[serde(rename = "type", skip_serializing_if = "Option::is_none", default)]
    pub source_type: Option<String>,

    /// Forge coordinates, parsed from `url` rather than declared.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub host: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub owner: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub repo: Option<String>,

    /// Channel name, for `type = "channel"`.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub channel: Option<String>,
    /// Directory on the evaluating machine, for `type = "path"`.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub path: Option<String>,
    /// Clone or download url, as declared.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub url: Option<String>,
    /// Where redirects from `url` landed at lock time. This, not `url`, is what `fetch`
    /// downloads.
    #[serde(
        rename = "resolvedUrl",
        skip_serializing_if = "Option::is_none",
        default
    )]
    pub resolved_url: Option<String>,
    /// Version a channel's resolved url names, e.g. `nixos-26.11pre1070770.8ce4ef6cb6f8`.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub version: Option<String>,

    /// Semver range the pin tracks. Re-picked on every update, so `tag` can move.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub release: Option<String>,
    /// Tag the pin names, or the one `release` picked.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub tag: Option<String>,
    /// Commit this pin resolved to.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub rev: Option<String>,
    /// Git ref `rev` can be found under; `HEAD` when the declaration named none.
    #[serde(rename = "ref", skip_serializing_if = "Option::is_none", default)]
    pub reference: Option<String>,

    /// Declared `fetchGit` options, repeated into [`Fetch::Git`].
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub submodules: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub shallow: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub lfs: Option<bool>,
    #[serde(
        rename = "exportIgnore",
        skip_serializing_if = "Option::is_none",
        default
    )]
    pub export_ignore: Option<bool>,

    /// NAR hash of the fetched tree, or the flat hash of the bytes for a `file`. Absent
    /// for `git` and `path`, which have nothing to check against.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub hash: Option<Hash>,
    /// Commit or archive timestamp, unix seconds. It reaches the version suffix of a
    /// NixOS build, so an entry missing it is incomplete rather than merely terse.
    #[serde(
        rename = "lastModified",
        skip_serializing_if = "Option::is_none",
        default
    )]
    pub last_modified: Option<i64>,

    /// Let the resolver probe a *patched* tree for a `flake.nix` and import its modules.
    /// Costs an import-from-derivation, so it is opt-in and meaningless without `patches`.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub importable: Option<bool>,
    /// Sub-input name to pin name, for a flake pin's own inputs.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub follows: Option<BTreeMap<String, String>>,
    /// Sub-inputs to leave at whatever the pinned source's own `flake.lock` says.
    #[serde(
        rename = "excludeFollow",
        skip_serializing_if = "Option::is_none",
        default
    )]
    pub exclude_follow: Option<Vec<String>>,
    /// Evaluate the tree as a flake instead of handing back a source-info attribute set.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub flake: Option<bool>,
    /// Subdirectory holding the flake, relative to the tree's root.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub dir: Option<String>,

    /// What the resolver fetches, derived from the fields above at lock time.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub fetch: Option<Fetch>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub patches: Option<Vec<Patch>>,
    /// Recursive SRI hash of the patched tree. It makes applying the patches a
    /// fixed-output derivation, so the patched store path comes from the lock instead of
    /// from whichever nixpkgs applied the diff.
    #[serde(
        rename = "patchedHash",
        skip_serializing_if = "Option::is_none",
        default
    )]
    pub patched_hash: Option<Hash>,

    /// Fields this build does not know, preserved on rewrite. See [`Rest`].
    #[serde(flatten)]
    pub rest: Rest,
}

/// A whole lock file: `{ "schema": …, "pnix": …, "pins": { … } }`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Lock {
    /// Shape of this file; [`SCHEMA`] is the only one this build accepts.
    pub schema: u32,
    /// Version of pnix that wrote the file, for triage. Deliberately outside the schema:
    /// the vendored resolver reads `schema` and `pins` and nothing else, so an older
    /// resolver ignores this field rather than misreading it.
    #[serde(rename = "pnix", skip_serializing_if = "Option::is_none", default)]
    pub writer: Option<String>,
    /// Pins by name. A [`BTreeMap`], so the file is in name order by construction.
    pub pins: BTreeMap<String, Pin>,
}

impl Lock {
    /// A lock for this build to write: current [`SCHEMA`], stamped with this crate's
    /// version.
    pub fn new(pins: BTreeMap<String, Pin>) -> Self {
        Lock {
            schema: SCHEMA,
            writer: Some(env!("CARGO_PKG_VERSION").to_owned()),
            pins,
        }
    }

    /// Parse a lock. `path` is used for error messages only.
    ///
    /// Only [`SCHEMA`] is accepted. The python implementation migrated an older lock
    /// forward instead; this build has never written one, and re-resolving from
    /// declarations would move every pin that named no explicit `rev`, so it refuses and
    /// says what to run (`DECISIONS.md`, D1).
    ///
    /// # Errors
    ///
    /// [`Error::Schema`] when `schema` is absent or is not [`SCHEMA`]; the message says
    /// whether to upgrade pnix or to migrate the lock. [`Error::Parse`] for malformed
    /// JSON, a mistyped field, or a hash that is not sha256.
    pub fn parse(text: &str, path: &Path) -> Result<Self> {
        let doc: Value = serde_json::from_str(text).map_err(|err| Error::Parse {
            path: path.to_path_buf(),
            err,
        })?;

        match doc.get("schema").and_then(Value::as_u64) {
            Some(found) if found == u64::from(SCHEMA) => {}
            Some(found) if found > u64::from(SCHEMA) => {
                return Err(Error::Schema(format!(
                    "{}: lock schema {found} but this pnix speaks {SCHEMA}. A newer \
                     pnix wrote it; upgrade pnix rather than downgrading the lock.",
                    path.display()
                )));
            }
            Some(found) => {
                return Err(Error::Schema(format!(
                    "{}: lock schema {found}, and this pnix reads {SCHEMA} only. Run \
                     the python pnix once to migrate it, then retry.",
                    path.display()
                )));
            }
            None => {
                return Err(Error::Schema(format!(
                    "{}: no `schema` field. This is not a pnix lock.",
                    path.display()
                )));
            }
        }

        serde_json::from_value(doc).map_err(|err| Error::Parse {
            path: path.to_path_buf(),
            err,
        })
    }

    /// Read a lock from disk.
    ///
    /// A missing file reads as a lock with no pins: `pnix update` before `pnix init` is a
    /// legitimate order, so absence is a state and not a failure.
    ///
    /// # Errors
    ///
    /// [`Error::Io`] if the file exists but cannot be read, plus everything
    /// [`Lock::parse`] reports.
    pub fn read(path: &Path) -> Result<Self> {
        match fs::read_to_string(path) {
            Ok(text) => Lock::parse(&text, path),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                Ok(Lock::new(BTreeMap::new()))
            }
            Err(err) => Err(Error::Io {
                path: path.to_path_buf(),
                err,
            }),
        }
    }

    /// The file's exact bytes: two-space indent, trailing newline, pins in name order.
    pub fn to_json(&self) -> String {
        let mut text = serde_json::to_string_pretty(self)
            .expect("a Lock is always serialisable: no maps with non-string keys");
        text.push('\n');
        text
    }

    /// Write the lock to `path`, creating its directory if needed.
    ///
    /// The bytes go to a sibling `<path>.tmp`, are flushed to disk, and are then renamed
    /// over `path`. The lock is the only record of what each pin resolved to, so a reader
    /// or a crash must never see it half-written.
    ///
    /// # Errors
    ///
    /// [`Error::Io`] naming the file it failed on. A failed write removes the temporary
    /// file and leaves `path` as it was.
    pub fn write(&self, path: &Path) -> Result<()> {
        let io = |err| Error::Io {
            path: path.to_path_buf(),
            err,
        };

        if let Some(parent) = path.parent()
            && !parent.as_os_str().is_empty()
        {
            fs::create_dir_all(parent).map_err(|err| Error::Io {
                path: parent.to_path_buf(),
                err,
            })?;
        }

        let mut temp = path.as_os_str().to_owned();
        temp.push(".tmp");
        let temp = PathBuf::from(temp);

        let written = (|| -> std::io::Result<()> {
            let mut file = fs::File::create(&temp)?;
            file.write_all(self.to_json().as_bytes())?;
            file.sync_all()
        })();

        if let Err(err) = written {
            let _ = fs::remove_file(&temp);
            return Err(Error::Io { path: temp, err });
        }

        fs::rename(&temp, path).map_err(io)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PNIX_LOCK: &str = include_str!("../tests/fixtures/pnix.lock.json");
    const NIXCONFIG_LOCK: &str = include_str!("../tests/fixtures/nixconfig.lock.json");
    /// Covers the source and patch shapes the two real locks happen not to contain.
    const SHAPES_LOCK: &str = include_str!("../tests/fixtures/shapes.lock.json");

    fn parse(text: &str) -> Lock {
        Lock::parse(text, Path::new("pins.lock.json")).expect("fixture parses")
    }

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("pnix-lock-test-{}-{tag}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("temp dir");
        dir
    }

    #[test]
    fn rewrites_pnixs_own_lock_byte_for_byte() {
        assert_eq!(parse(PNIX_LOCK).to_json(), PNIX_LOCK);
    }

    #[test]
    fn rewrites_the_nixconfig_lock_byte_for_byte() {
        let lock = parse(NIXCONFIG_LOCK);
        assert_eq!(lock.pins.len(), 21, "all 21 pins survive the round trip");
        assert_eq!(lock.to_json(), NIXCONFIG_LOCK);
    }

    #[test]
    fn rewrites_every_source_shape_byte_for_byte() {
        let lock = parse(SHAPES_LOCK);
        assert_eq!(lock.to_json(), SHAPES_LOCK);
    }

    #[test]
    fn reads_the_shapes_the_real_locks_do_not_contain() {
        let lock = parse(SHAPES_LOCK);

        let channel = &lock.pins["channelpin"];
        assert_eq!(channel.channel.as_deref(), Some("nixos-unstable"));
        assert!(matches!(channel.fetch, Some(Fetch::Tarball { .. })));

        assert!(matches!(
            lock.pins["filepin"].fetch,
            Some(Fetch::File { .. })
        ));
        assert!(matches!(
            lock.pins["localpin"].fetch,
            Some(Fetch::Path { .. })
        ));

        let release = &lock.pins["releasepin"];
        assert_eq!(release.release.as_deref(), Some("^1.3"));
        assert_eq!(release.tag.as_deref(), Some("v1.4.2"));

        let patched = lock.pins["patchedpin"].patches.as_ref().expect("patches");
        assert_eq!(patched.len(), 4);
        assert!(matches!(patched[0], Patch::Path { .. }));
        assert!(matches!(patched[1], Patch::Pr { number: 181, .. }));
        assert!(matches!(patched[2], Patch::Commit { .. }));
        assert!(matches!(patched[3], Patch::Url { .. }));
        assert!(lock.pins["patchedpin"].patched_hash.is_some());
    }

    #[test]
    fn an_unknown_field_survives_a_round_trip() {
        let lock = parse(SHAPES_LOCK);
        let future = &lock.pins["futurepin"];

        assert_eq!(
            future.rest.get("signedBy").and_then(Value::as_str),
            Some("a-provenance-field-this-build-never-heard-of")
        );
        let Some(Fetch::Git { rest, .. }) = &future.fetch else {
            panic!("futurepin is a git fetch");
        };
        assert!(
            rest.contains_key("sparseCheckout"),
            "fetch fields are a flat passthrough"
        );
    }

    #[test]
    fn a_lock_without_a_schema_field_is_refused() {
        let err =
            Lock::parse(r#"{"pins": {}}"#, Path::new("/p/pins.lock.json")).expect_err("no schema");
        let msg = err.to_string();
        assert!(msg.contains("/p/pins.lock.json"), "names the file: {msg}");
        assert!(msg.contains("not a pnix lock"), "says what it is: {msg}");
    }

    #[test]
    fn a_lock_from_a_newer_pnix_is_refused() {
        let text = format!(r#"{{"schema": {}, "pins": {{}}}}"#, SCHEMA + 1);
        let msg = Lock::parse(&text, Path::new("pins.lock.json"))
            .expect_err("future schema")
            .to_string();
        assert!(
            msg.contains("upgrade pnix"),
            "says which side to move: {msg}"
        );
    }

    #[test]
    fn an_older_schema_is_refused_with_the_way_out() {
        let text = format!(r#"{{"schema": {}, "pins": {{}}}}"#, SCHEMA - 1);
        let msg = Lock::parse(&text, Path::new("pins.lock.json"))
            .expect_err("old schema")
            .to_string();
        assert!(
            msg.contains("python pnix"),
            "names the migration path: {msg}"
        );
    }

    #[test]
    fn a_hash_that_is_not_sha256_is_refused() {
        for bad in [
            r#"{"schema": 5, "pins": {"p": {"hash": "blake3-abc"}}}"#,
            r#"{"schema": 5, "pins": {"p": {"hash": "sha256-"}}}"#,
            r#"{"schema": 5, "pins": {"p": {"hash": "deadbeef"}}}"#,
        ] {
            let msg = Lock::parse(bad, Path::new("pins.lock.json"))
                .expect_err("bad hash")
                .to_string();
            assert!(msg.contains("sha256"), "explains the constraint: {msg}");
        }
        assert!(Hash::parse("sha256-ShMVBHmTnReG1GD+VQaw6xwyuYbabLZPS28xSfajiaA=").is_ok());
    }

    #[test]
    fn pins_are_written_in_name_order() {
        let mut pins = BTreeMap::new();
        for name in ["zlib", "authentik", "mango"] {
            pins.insert(name.to_owned(), Pin::default());
        }
        let text = Lock::new(pins).to_json();
        let found: Vec<usize> = ["authentik", "mango", "zlib"]
            .iter()
            .map(|n| text.find(n).expect("present"))
            .collect();
        assert!(found.windows(2).all(|w| w[0] < w[1]), "sorted: {text}");
    }

    #[test]
    fn a_new_lock_is_stamped_with_this_build() {
        let lock = Lock::new(BTreeMap::new());
        assert_eq!(lock.schema, SCHEMA);
        assert_eq!(lock.writer.as_deref(), Some(env!("CARGO_PKG_VERSION")));
        assert!(lock.to_json().ends_with("}\n"), "trailing newline");
    }

    #[test]
    fn a_missing_lock_reads_as_no_pins() {
        let dir = scratch("missing");
        let lock = Lock::read(&dir.join("pins.lock.json")).expect("absence is a state");
        assert!(lock.pins.is_empty());
        assert_eq!(lock.schema, SCHEMA);
    }

    #[test]
    fn writing_creates_the_lock_directory() {
        let dir = scratch("mkdir");
        let path = dir.join(".pnix").join("pins.lock.json");
        parse(PNIX_LOCK).write(&path).expect("writes");
        assert_eq!(fs::read_to_string(&path).expect("read back"), PNIX_LOCK);
    }

    /// A rename replaces the inode; an in-place write keeps it. Without this, dropping the
    /// temporary file passes every other test here.
    #[test]
    fn writing_renames_a_new_file_into_place() {
        use std::os::unix::fs::MetadataExt as _;

        let dir = scratch("rename");
        let path = dir.join("pins.lock.json");
        parse(PNIX_LOCK).write(&path).expect("writes");
        let first = fs::metadata(&path).expect("stat").ino();

        parse(SHAPES_LOCK).write(&path).expect("rewrites");
        let second = fs::metadata(&path).expect("stat").ino();

        assert_ne!(
            first, second,
            "the lock was written in place, not renamed over"
        );
    }

    #[test]
    fn writing_replaces_the_old_lock_and_leaves_no_temp_file() {
        let dir = scratch("atomic");
        let path = dir.join("pins.lock.json");
        fs::write(&path, "stale").expect("seed");

        parse(SHAPES_LOCK).write(&path).expect("writes");

        assert_eq!(fs::read_to_string(&path).expect("read back"), SHAPES_LOCK);
        let leftovers: Vec<_> = fs::read_dir(&dir)
            .expect("list")
            .map(|e| e.expect("entry").file_name())
            .filter(|n| n != "pins.lock.json")
            .collect();
        assert!(leftovers.is_empty(), "temp file left behind: {leftovers:?}");
    }
}
