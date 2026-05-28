//! Integration tests for the diagnosis-mode system prompt builder.
//!
//! These live outside the lib test target so they don't have to compile
//! alongside the Windows-incompatible `mcp_stdio` / `mcp_tool_bridge` lib
//! tests (which rely on the unix-only `PermissionsExt::set_mode`).

use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use runtime::{today_utc_ymd, ProjectContext, SystemPromptBuilder};

const SYSTEM_PROMPT_DYNAMIC_BOUNDARY: &str = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__";

fn temp_dir(label: &str) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("time should be after epoch")
        .as_nanos();
    std::env::temp_dir().join(format!("runtime-diagnosis-prompt-{label}-{nanos}"))
}

#[test]
fn diagnosis_builder_omits_software_engineering_sections() {
    let project_context = ProjectContext {
        cwd: PathBuf::from("/tmp/db-clinic"),
        current_date: "2026-05-27".to_string(),
        git_status: Some("## main".to_string()),
        git_diff: Some("diff --git a/x b/x".to_string()),
        ..ProjectContext::default()
    };

    let rendered = SystemPromptBuilder::for_diagnosis()
        .with_os("windows", "10")
        .with_project_context(project_context)
        .render();

    // Skipped: software-engineering framing + git project context + dynamic
    // boundary marker + runtime config + claude instructions (no files).
    assert!(!rendered.contains("software engineering tasks"));
    assert!(!rendered.contains("# Doing tasks"));
    assert!(!rendered.contains("# Project context"));
    assert!(!rendered.contains("# Runtime config"));
    assert!(!rendered.contains("# Claude instructions"));
    assert!(!rendered.contains(SYSTEM_PROMPT_DYNAMIC_BOUNDARY));
    // Git material never leaks even when the context carries it.
    assert!(!rendered.contains("## main"));
    assert!(!rendered.contains("diff --git"));

    // Kept: slim `# System`, actions/blast-radius warning, environment.
    assert!(rendered.contains("# System"));
    assert!(rendered.contains("prompt injection"));
    assert!(rendered.contains("context grows"));
    assert!(rendered.contains("# Executing actions with care"));
    assert!(rendered.contains("# Environment context"));
    assert!(rendered.contains("Model family: an AI assistant"));
    assert!(!rendered.contains("Claude Opus 4.6"));
    assert!(rendered.contains("Working directory: /tmp/db-clinic"));
    assert!(rendered.contains("Date: 2026-05-27"));
    assert!(rendered.contains("Platform: windows 10"));
}

#[test]
fn diagnosis_builder_includes_repo_local_instruction_files() {
    let root = temp_dir("repo-local");
    fs::create_dir_all(&root).expect("root dir");
    fs::write(root.join("CLAUDE.md"), "Cluster runs MySQL 8.0.34").expect("write CLAUDE.md");

    let project_context =
        ProjectContext::discover_at(&root, "2026-05-27").expect("context should load");
    let rendered = SystemPromptBuilder::for_diagnosis()
        .with_os("linux", "x86_64")
        .with_project_context(project_context)
        .render();

    assert!(rendered.contains("# Claude instructions"));
    assert!(rendered.contains("Cluster runs MySQL 8.0.34"));

    fs::remove_dir_all(root).expect("cleanup temp dir");
}

#[test]
fn discover_at_does_not_walk_ancestor_directories() {
    let root = temp_dir("ancestor-walk");
    let nested = root.join("workspace");
    fs::create_dir_all(&nested).expect("nested dir");
    fs::write(root.join("CLAUDE.md"), "parent rules").expect("write parent");
    fs::write(nested.join("CLAUDE.md"), "workspace rules").expect("write nested");

    let context = ProjectContext::discover_at(&nested, "2026-05-27").expect("context should load");

    assert_eq!(context.instruction_files.len(), 1);
    assert_eq!(context.instruction_files[0].content, "workspace rules");

    fs::remove_dir_all(root).expect("cleanup temp dir");
}

#[test]
fn today_utc_ymd_is_well_formed() {
    let s = today_utc_ymd();
    assert_eq!(s.len(), 10);
    assert_eq!(&s[4..5], "-");
    assert_eq!(&s[7..8], "-");
    let year: i32 = s[0..4].parse().expect("year parses");
    assert!((2020..=2100).contains(&year));
    let month: u32 = s[5..7].parse().expect("month parses");
    assert!((1..=12).contains(&month));
    let day: u32 = s[8..10].parse().expect("day parses");
    assert!((1..=31).contains(&day));
}
