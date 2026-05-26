use session_persistence::{
    Checkpoint, CompactionRecord, MemoryBackend, MessageRecord, PersistenceError,
    PromptHistoryRecord, SessionBackend, SessionRecord,
};

use std::path::PathBuf;
use std::sync::Arc;

fn create_test_record(id: &str) -> SessionRecord {
    SessionRecord {
        session_id: id.to_string(),
        created_at_ms: 1000,
        updated_at_ms: 1000,
        model: Some("test-model".to_string()),
        workspace_root: None,
        fork_parent_id: None,
        fork_branch_name: None,
    }
}

fn create_message_record(sid: &str, role: &str, step: usize) -> MessageRecord {
    MessageRecord {
        session_id: sid.to_string(),
        role: role.to_string(),
        content_json: r#"{"text":"hello"}"#.to_string(),
        timestamp_ms: 2000,
        step_index: step,
        usage_json: None,
    }
}

fn create_checkpoint(sid: &str, step: usize) -> Checkpoint {
    Checkpoint {
        checkpoint_id: format!("cp-{sid}-{step}"),
        session_id: sid.to_string(),
        step_index: step,
        timestamp_ms: 3000,
        message_count: 10,
        summary: Some("test summary".to_string()),
    }
}

fn test_backend_trait_consistency<B: SessionBackend>(backend: &B) {
    let sid = "test-session-1";

    let record = create_test_record(sid);
    backend.save_session_meta(&record).unwrap();

    let loaded = backend.load_session_meta(sid).unwrap();
    assert!(loaded.is_some());
    let loaded = loaded.unwrap();
    assert_eq!(loaded.session_id, sid);
    assert_eq!(loaded.created_at_ms, 1000);

    let sessions = backend.list_sessions().unwrap();
    assert_eq!(sessions.len(), 1);
    assert_eq!(sessions[0].session_id, sid);

    let msg = create_message_record(sid, "user", 0);
    backend.append_message(&msg).unwrap();

    let count = backend.message_count(sid).unwrap();
    assert_eq!(count, 1);

    let messages = backend.load_messages(sid).unwrap();
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0].role, "user");

    let cp = create_checkpoint(sid, 0);
    backend.save_checkpoint(&cp).unwrap();

    let latest_cp = backend.load_latest_checkpoint(sid).unwrap();
    assert!(latest_cp.is_some());
    assert_eq!(latest_cp.unwrap().step_index, 0);

    let cp2 = create_checkpoint(sid, 1);
    backend.save_checkpoint(&cp2).unwrap();

    let latest = backend.load_latest_checkpoint(sid).unwrap();
    assert!(latest.is_some());
    assert_eq!(latest.unwrap().step_index, 1);

    let cps = backend.list_checkpoints(sid).unwrap();
    assert_eq!(cps.len(), 2);

    let cp_at_0 = backend.load_checkpoint_at_step(sid, 0).unwrap();
    assert!(cp_at_0.is_some());
    assert_eq!(cp_at_0.unwrap().step_index, 0);

    let history = PromptHistoryRecord {
        session_id: sid.to_string(),
        timestamp_ms: 4000,
        text: "hello world".to_string(),
    };
    backend.save_prompt_history(&history).unwrap();

    let hist = backend.load_prompt_history(sid).unwrap();
    assert_eq!(hist.len(), 1);
    assert_eq!(hist[0].text, "hello world");

    let compaction = CompactionRecord {
        session_id: sid.to_string(),
        count: 1,
        removed_message_count: 5,
        summary: "compacted".to_string(),
        timestamp_ms: 5000,
    };
    backend.save_compaction(&compaction).unwrap();

    let comp = backend.load_compaction(sid).unwrap();
    assert!(comp.is_some());
    assert_eq!(comp.unwrap().count, 1);

    let deleted = backend.delete_session(sid).unwrap();
    assert!(deleted);

    let after_delete = backend.load_session_meta(sid).unwrap();
    assert!(after_delete.is_none());
}

#[test]
fn test_memory_backend() {
    let backend = MemoryBackend::new();
    test_backend_trait_consistency(&backend);
}

#[test]
fn test_jsonl_backend() {
    let tmp_dir = std::env::temp_dir().join("sp-test-jsonl");
    std::fs::create_dir_all(&tmp_dir).ok();
    let backend = session_persistence::JsonlBackend::open(&tmp_dir).unwrap();
    test_backend_trait_consistency(&backend);
    std::fs::remove_dir_all(&tmp_dir).ok();
}
#[cfg(feature = "sqlite")]
#[test]
fn test_sqlite_backend() {
    let tmp_dir = std::env::temp_dir().join("sp-test-sqlite");
    std::fs::create_dir_all(&tmp_dir).ok();
    let backend = session_persistence::SqliteBackend::open(&tmp_dir).unwrap();
    test_backend_trait_consistency(&backend);
    std::fs::remove_dir_all(&tmp_dir).ok();
}