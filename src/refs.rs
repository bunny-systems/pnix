//! Resolve a git ref to a revision with `git ls-remote`.
//!
//! Works for all git forges.
//!
//! The subprocess is confined to [`ls_remote`]. Everything above it — parsing, ref
//! precedence, tag peeling — is a pure function over [`Row`]s, so it is testable without a
//! network.

use std::collections::BTreeMap;
use std::fmt;
use std::process::Command;

/// Suffix git gives the commit an annotated tag points at: `refs/tags/v1^{}`.
///
/// `git ls-remote <url> <tag>` never emits this row on its own, so the peel pattern has to
/// be asked for as a second pattern.
pub const PEELED: &str = "^{}";

/// Length of a full git object id in hex.
pub const SHA_LEN: usize = 40;

/// One line of `git ls-remote` output: an object id and the ref naming it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub sha: String,
    pub name: String,
}

#[derive(Debug)]
pub enum Error {
    /// `git` could not be run at all.
    Spawn(std::io::Error),
    /// `git ls-remote` exited non-zero; holds the command line and its stderr.
    Remote {
        url: String,
        argv: Vec<String>,
        stderr: String,
    },
    /// The remote answered, but nothing it returned is the ref that was asked for.
    NoRef { url: String, wanted: String },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Spawn(err) => write!(f, "git ls-remote: {err}"),
            Error::Remote { argv, stderr, .. } => {
                write!(f, "git {}: {stderr}", argv.join(" "))
            }
            Error::NoRef { url, wanted } => write!(f, "{url}: no ref matching {wanted:?}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(err: std::io::Error) -> Self {
        Error::Spawn(err)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

/// The four types of request pnix mkaes.
/// Flags are added based on the type of query to make lookup faster.
///
/// On a remote with few refs the difference is unmeasurable, so this is insurance against
/// large or PR-heavy remotes rather than a general speedup.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum Query {
    /// The remote's `HEAD`.
    ///
    /// Unfilterable.
    /// Always costs full advertisment.
    Head,
    /// One ref by name: a branch, a tag, a fully qualified ref, or a pull-request ref.
    ///
    /// The name decides the filter, wrong one hides the ref.
    Ref(String),
    /// One tag by its literal name.
    Tag(String),
    /// Every ref under `refs/tags/`.
    Tags,
}

impl Query {
    pub fn reference(name: impl Into<String>) -> Self {
        Query::Ref(name.into())
    }

    pub fn tag(name: impl Into<String>) -> Self {
        Query::Tag(name.into())
    }

    /// Flags that turn into server-side `ref-prefix` arguments.
    fn filters(&self) -> &'static [&'static str] {
        match self {
            Query::Head => &[],
            Query::Ref(name) => match name.as_str() {
                n if n.starts_with("refs/heads/") => &["--branches"],
                n if n.starts_with("refs/tags/") => &["--tags"],
                // An unqualified name may be either
                n if !n.contains('/') => &["--tags", "--branches"],
                // refs/pull/…, refs/merge-requests/…
                _ => &[],
            },
            Query::Tag(_) | Query::Tags => &["--tags"],
        }
    }

    /// Ref patterns, in command-line order.
    fn patterns(&self) -> Vec<String> {
        match self {
            Query::Head => vec!["HEAD".to_string()],
            Query::Ref(name) => vec![name.clone(), format!("{name}{PEELED}")],
            Query::Tag(_) | Query::Tags => vec![],
        }
    }

    /// Creates the candidates for selection.
    ///
    /// `Head` -> `HEAD`.
    /// `Ref(r)` -> `refs/tags/<r>^{}`, `refs/tags/<r>`, `refs/heads/<r>`, `<r>^{}`, `<r>`.
    /// `Tag(t)` -> `refs/tags/<t>^{}`, `refs/tags/<t>`
    fn candidates(&self) -> Vec<String> {
        match self {
            Query::Head => vec!["HEAD".to_string()],
            Query::Ref(name) => vec![
                format!("refs/tags/{name}{PEELED}"),
                format!("refs/tags/{name}"),
                format!("refs/heads/{name}"),
                format!("{name}{PEELED}"),
                name.to_string(),
            ],
            Query::Tag(name) => vec![
                format!("refs/tags/{name}{PEELED}"),
                format!("refs/tags/{name}"),
            ],
            Query::Tags => vec![],
        }
    }

    /// The sha this query selects out of `rows`.
    ///
    /// Candidates outer, rows inner: git sorts its output by ref name, so scanning rows
    /// first would let `refs/heads/x` answer before `refs/tags/x`.
    pub fn pick(&self, rows: &[Row]) -> Option<String> {
        for candidate in &self.candidates() {
            if let Some(row) = rows.iter().find(|row| row.name == *candidate) {
                return Some(row.sha.clone());
            }
        }
        None
    }

    pub fn argv(&self, url: &str) -> Vec<String> {
        let mut out = vec!["ls-remote".to_string()];
        out.extend(self.filters().iter().map(|f| f.to_string()));
        out.push(url.to_string());
        out.extend(self.patterns());
        out
    }
}

/// Parse `git ls-remote` stdout to a vector of rows with name and sha.
pub fn parse_rows(stdout: &str) -> Vec<Row> {
    let mut rows = Vec::new();
    for line in stdout.lines() {
        if let Some((sha, name)) = line.split_once('\t') {
            rows.push(Row {
                sha: sha.trim().to_owned(),
                name: name.trim().to_owned(),
            });
        }
    }
    rows
}

/// Every tag in `rows`, mapped to the **commit** it names.
///
/// `--tags` lists an annotated tag twice, so the peeled row must win.
pub fn tags_from_rows(rows: &[Row]) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    for row in rows {
        let (name, peeled) = match row.name.strip_suffix(PEELED) {
            Some(bare) => (bare, true),
            None => (row.name.as_str(), false),
        };
        let Some(tag) = name.strip_prefix("refs/tags/") else {
            continue;
        };
        // A peeled row always wins
        if peeled || !out.contains_key(tag) {
            out.insert(tag.to_owned(), row.sha.clone());
        }
    }
    out
}
/// Run one git remote lookup.
pub fn ls_remote(url: &str, query: &Query) -> Result<Vec<Row>> {
    let argv = query.argv(url);
    let output = Command::new("git").args(&argv).output()?;
    if !output.status.success() {
        return Err(Error::Remote {
            url: url.to_owned(),
            argv,
            stderr: String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        });
    }
    Ok(parse_rows(&String::from_utf8_lossy(&output.stdout)))
}

/// The sha a query selects.
pub fn resolve(url: &str, query: &Query) -> Result<String> {
    let wanted = match query {
        Query::Head => "HEAD".to_owned(),
        Query::Ref(name) | Query::Tag(name) => name.clone(),
        Query::Tags => panic!("resolve: Query::Tags has no single sha, call tags() instead"),
    };
    let rows = ls_remote(url, query)?;
    query.pick(&rows).ok_or(Error::NoRef {
        url: url.to_owned(),
        wanted,
    })
}

/// Every tag on the remote as a `--tags` listing.
pub fn tags(url: &str) -> Result<BTreeMap<String, String>> {
    Ok(tags_from_rows(&ls_remote(url, &Query::Tags)?))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn rows(pairs: &[(&str, &str)]) -> Vec<Row> {
        pairs
            .iter()
            .map(|(sha, name)| Row {
                sha: (*sha).to_owned(),
                name: (*name).to_owned(),
            })
            .collect()
    }

    const A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const C: &str = "cccccccccccccccccccccccccccccccccccccccc";

    /// Every lookup's exact command line. The filter column is the whole point: it is what
    /// git turns into a server-side `ref-prefix`.
    #[test]
    fn argv_is_the_command_line_for_each_lookup() {
        let cases: &[(Query, &[&str])] = &[
            (Query::Head, &["ls-remote", "u", "HEAD"]),
            (
                Query::reference("main"),
                &["ls-remote", "--tags", "--branches", "u", "main", "main^{}"],
            ),
            (
                Query::reference("refs/heads/main"),
                &[
                    "ls-remote",
                    "--branches",
                    "u",
                    "refs/heads/main",
                    "refs/heads/main^{}",
                ],
            ),
            (
                Query::reference("refs/tags/v1"),
                &[
                    "ls-remote",
                    "--tags",
                    "u",
                    "refs/tags/v1",
                    "refs/tags/v1^{}",
                ],
            ),
            (Query::tag("v2.0.0"), &["ls-remote", "--tags", "u"]),
            (Query::Tags, &["ls-remote", "--tags", "u"]),
        ];

        for (query, expected) in cases {
            assert_eq!(query.argv("u"), *expected, "{query:?}");
        }
    }

    /// A filter that does not cover the ref returns zero rows instead of failing, so a
    /// pull-request ref — which pnix tracks (D10) — must go unfiltered.
    #[test]
    fn argv_leaves_a_pull_request_ref_unfiltered() {
        assert_eq!(
            Query::reference("refs/pull/181/head").argv("u"),
            vec![
                "ls-remote",
                "u",
                "refs/pull/181/head",
                "refs/pull/181/head^{}"
            ]
        );
    }

    /// `HEAD` is the common case and cannot be filtered: no flag yields `ref-prefix HEAD`.
    #[test]
    fn argv_asks_for_head_without_a_filter() {
        let line = Query::Head.argv("u");
        assert!(
            !line.iter().any(|a| a.starts_with("--")),
            "no filter flag can select HEAD: {line:?}"
        );
    }

    #[test]
    fn parse_rows_splits_on_the_tab() {
        let out = format!("{A}\trefs/heads/main\n{B}\tHEAD\n");
        assert_eq!(
            parse_rows(&out),
            rows(&[(A, "refs/heads/main"), (B, "HEAD")])
        );
    }

    #[test]
    fn parse_rows_skips_blank_lines() {
        let out = format!("\n{A}\tHEAD\n\n");
        assert_eq!(parse_rows(&out).len(), 1);
    }

    #[test]
    fn pick_prefers_the_peeled_row_of_an_annotated_tag() {
        let r = rows(&[(A, "refs/tags/v1.0.0"), (B, "refs/tags/v1.0.0^{}")]);
        assert_eq!(Query::reference("v1.0.0").pick(&r).as_deref(), Some(B));
    }

    #[test]
    fn pick_prefers_a_tag_over_a_branch_of_the_same_name() {
        let r = rows(&[(A, "refs/heads/release"), (B, "refs/tags/release")]);
        assert_eq!(Query::reference("release").pick(&r).as_deref(), Some(B));
    }

    // A branch found by its short name: the one path no other test exercises, because
    // every case above either prefers a tag or passes an already-qualified name.
    #[test]
    fn pick_finds_a_branch_by_its_short_name() {
        let r = rows(&[(A, "refs/heads/main"), (B, "HEAD")]);
        assert_eq!(Query::reference("main").pick(&r).as_deref(), Some(A));
    }

    #[test]
    fn pick_matches_exact_names_only() {
        let r = rows(&[(A, "refs/tags/main-thing"), (B, "refs/heads/main-other")]);
        assert_eq!(
            Query::reference("main").pick(&r),
            None,
            "a tail match is not a match"
        );
    }

    #[test]
    fn pick_takes_a_fully_qualified_ref_as_given() {
        let r = rows(&[(A, "refs/heads/nixos-unstable")]);
        assert_eq!(
            Query::reference("refs/heads/nixos-unstable")
                .pick(&r)
                .as_deref(),
            Some(A)
        );
    }

    #[test]
    fn pick_tag_prefers_the_commit_over_the_tag_object() {
        let r = rows(&[(A, "refs/tags/v1.0.0"), (B, "refs/tags/v1.0.0^{}")]);
        assert_eq!(Query::tag("v1.0.0").pick(&r).as_deref(), Some(B));
    }

    #[test]
    fn pick_tag_takes_a_lightweight_tag() {
        let r = rows(&[(A, "refs/tags/v1.0.0")]);
        assert_eq!(Query::tag("v1.0.0").pick(&r).as_deref(), Some(A));
    }

    // The property `tag` exists for: a branch of the same name is not an answer.
    #[test]
    fn pick_tag_never_accepts_a_branch() {
        let r = rows(&[(A, "refs/heads/v1.0.0"), (C, "HEAD")]);
        assert_eq!(Query::tag("v1.0.0").pick(&r), None);
    }

    #[test]
    fn pick_tag_ignores_a_different_tag() {
        let r = rows(&[(A, "refs/tags/v1.0.1"), (B, "refs/tags/v1.0.10")]);
        assert_eq!(
            Query::tag("v1.0.1").pick(&r).as_deref(),
            Some(A),
            "exact names only"
        );
        assert_eq!(Query::tag("v1.0.2").pick(&r), None);
    }

    /// A literal tag and the whole list are one request; only the selection differs.
    #[test]
    fn a_literal_tag_and_the_tag_list_ask_the_same_thing() {
        assert_eq!(Query::tag("v1").argv("u"), Query::Tags.argv("u"));
    }

    #[test]
    fn the_tag_list_selects_nothing_on_its_own() {
        let r = rows(&[(A, "refs/tags/v1")]);
        assert_eq!(Query::Tags.pick(&r), None, "Tags has no single answer");
    }

    #[test]
    fn tags_from_rows_maps_an_annotated_tag_to_its_commit() {
        for order in [
            rows(&[(A, "refs/tags/v2.0.0"), (B, "refs/tags/v2.0.0^{}")]),
            rows(&[(B, "refs/tags/v2.0.0^{}"), (A, "refs/tags/v2.0.0")]),
        ] {
            let found = tags_from_rows(&order);
            assert_eq!(
                found.get("v2.0.0").map(String::as_str),
                Some(B),
                "the peeled row must win whichever order it arrives in"
            );
        }
    }

    #[test]
    fn tags_from_rows_keeps_a_lightweight_tag() {
        let found = tags_from_rows(&rows(&[(A, "refs/tags/v1")]));
        assert_eq!(found.get("v1").map(String::as_str), Some(A));
    }

    #[test]
    fn tags_from_rows_ignores_anything_that_is_not_a_tag() {
        let found = tags_from_rows(&rows(&[(A, "refs/heads/main"), (C, "HEAD")]));
        assert!(found.is_empty(), "got {found:?}");
    }

    /// A throwaway repo on disk, used as a local `ls-remote` URL: one commit on `main`,
    /// one tag `v1`. Returns the repo path and that commit's sha. No network.
    fn scratch_repo(name: &str) -> (PathBuf, String) {
        let dir =
            std::env::temp_dir().join(format!("pnix-refs-test-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("temp dir");

        let git = |args: &[&str]| {
            let out = Command::new("git")
                .args(["-C", dir.to_str().expect("utf-8 path")])
                .args(["-c", "user.name=pnix", "-c", "user.email=pnix@invalid"])
                .args(args)
                .output()
                .expect("run git");
            assert!(
                out.status.success(),
                "git {args:?}: {}",
                String::from_utf8_lossy(&out.stderr)
            );
            String::from_utf8_lossy(&out.stdout).trim().to_owned()
        };

        git(&["init", "--quiet", "--initial-branch=main"]);
        git(&["commit", "--quiet", "--allow-empty", "-m", "one"]);
        git(&["tag", "v1"]);
        let sha = git(&["rev-parse", "HEAD"]);
        (dir, sha)
    }

    #[test]
    fn a_local_repo_answers_head_its_branch_and_its_tag() {
        let (dir, sha) = scratch_repo("local");
        let url = dir.to_str().expect("utf-8 path");

        assert_eq!(resolve(url, &Query::Head).expect("HEAD"), sha);
        assert_eq!(
            resolve(url, &Query::reference("main")).expect("branch"),
            sha
        );
        assert_eq!(resolve(url, &Query::tag("v1")).expect("tag"), sha);
        assert_eq!(
            tags(url).expect("tags"),
            BTreeMap::from([("v1".to_owned(), sha)])
        );

        std::fs::remove_dir_all(&dir).expect("clean up");
    }

    /// Nothing on the remote matches, so the lookup itself succeeded: that is
    /// [`Error::NoRef`], never an empty `Ok`.
    #[test]
    fn a_missing_ref_is_an_error_not_an_empty_answer() {
        let (dir, _) = scratch_repo("missing");
        let url = dir.to_str().expect("utf-8 path");

        for query in [Query::reference("nope"), Query::tag("nope")] {
            match resolve(url, &query) {
                Err(Error::NoRef { wanted, .. }) => assert_eq!(wanted, "nope"),
                other => panic!("{query:?} should be NoRef, got {other:?}"),
            }
        }

        std::fs::remove_dir_all(&dir).expect("clean up");
    }

    /// A remote that cannot be reached at all is [`Error::Remote`], and it carries git's
    /// own stderr so the failure can be read without re-running the command.
    #[test]
    fn an_unreachable_remote_reports_what_git_said() {
        let missing = std::env::temp_dir().join(format!("pnix-refs-absent-{}", std::process::id()));
        let url = missing.to_str().expect("utf-8 path");

        match ls_remote(url, &Query::Head) {
            Err(Error::Remote { stderr, argv, .. }) => {
                assert!(!stderr.is_empty(), "stderr was dropped");
                assert!(
                    argv.contains(&url.to_owned()),
                    "argv was not recorded: {argv:?}"
                );
            }
            other => panic!("expected Error::Remote, got {other:?}"),
        }
    }

    // Run with `cargo test -- --ignored`. Named repo, no named tag: tags get renamed.
    #[test]
    #[ignore = "needs the network"]
    fn resolves_a_real_remote() {
        const URL: &str = "https://github.com/NixOS/nix";

        let head = resolve(URL, &Query::Head).expect("HEAD resolves");
        assert_eq!(head.len(), SHA_LEN);
        assert!(head.chars().all(|c| c.is_ascii_hexdigit()));

        let all = tags(URL).expect("tags listed");
        assert!(
            all.len() > 100,
            "NixOS/nix has many tags, got {}",
            all.len()
        );
        for (tag, sha) in &all {
            assert_eq!(sha.len(), SHA_LEN, "{tag} -> {sha}");
        }

        let (tag, sha) = all.iter().next().expect("at least one tag");
        assert_eq!(&resolve(URL, &Query::tag(tag)).expect("tag resolves"), sha);
        assert!(
            resolve(URL, &Query::tag("definitely-not-a-tag-9f3a")).is_err(),
            "a missing tag is an error, not a silent fallback"
        );
    }
}
