use std::fs;
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};

use crate::{
    Checkpoint, CompactionRecord, MessageRecord, PersistenceError, PromptHistoryRecord,
    SessionBackend, SessionRecord,
};

fn session_dir(root: &Path, session_id: &str) -> PathBuf {
    root.join("sessions").join(session_id)
}

fn user_session_dir(root: &Path, username: &str, session_id: &str) -> PathBuf {
    root.join("sessions").join(username).join(session_id)
}

fn meta_path(dir: &Path) -> PathBuf {
    dir.join("meta.json")
}

fn messages_path(dir: &Path) -> PathBuf {
    dir.join("messages.jsonl")
}

fn checkpoints_path(dir: &Path) -> PathBuf {
    dir.join("checkpoints.jsonl")
}

fn prompt_history_path(dir: &Path) -> PathBuf {
    dir.join("prompt_history.jsonl")
}

fn compaction_path(dir: &Path) -> PathBuf {
    dir.join("compaction.json")
}

fn append_jsonl_line(path: &Path, line: &str) -> Result<(), PersistenceError> {
    let file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    let mut writer = BufWriter::new(file);
    writer.write_all(line.as_bytes())?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    Ok(())
}

fn read_jsonl_lines(path: &Path) -> Result<Vec<String>, PersistenceError> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path)?;
    Ok(content
        .lines()
        .filter(|l| !l.is_empty())
        .map(String::from)
        .collect())
}

fn atomic_write(path: &Path, content: &str) -> Result<(), PersistenceError> {
    let tmp_path = path.with_extension("tmp");
    {
        let file = fs::File::create(&tmp_path)?;
        let mut writer = BufWriter::new(file);
        writer.write_all(content.as_bytes())?;
        writer.flush()?;
    }
    fs::rename(&tmp_path, path)?;
    Ok(())
}

pub struct JsonlBackend {
    root: PathBuf,
}

impl JsonlBackend {
    pub fn open(root: &Path) -> Result<Self, PersistenceError> {
        let sessions_dir = root.join("sessions");
        fs::create_dir_all(&sessions_dir)?;
        Ok(Self {
            root: root.to_path_buf(),
        })
    }
}

impl SessionBackend for JsonlBackend {
    fn save_session_meta(&self, record: &SessionRecord) -> Result<(), PersistenceError> {
        let dir = if let Some(username) = &record.username {
            user_session_dir(&self.root, username, &record.session_id)
        } else {
            session_dir(&self.root, &record.session_id)
        };
        fs::create_dir_all(&dir)?;
        let json = serde_json::to_string(record)?;
        atomic_write(&meta_path(&dir), &json)?;
        Ok(())
    }

    fn load_session_meta(
        &self,
        session_id: &str,
    ) -> Result<Option<SessionRecord>, PersistenceError> {
        let dir = session_dir(&self.root, session_id);
        let path = meta_path(&dir);
        if !path.exists() {
            return Ok(None);
        }
        let content = fs::read_to_string(&path)?;
        let record: SessionRecord = serde_json::from_str(&content)?;
        Ok(Some(record))
    }

    fn list_sessions(&self) -> Result<Vec<SessionRecord>, PersistenceError> {
        let sessions_dir = self.root.join("sessions");
        if !sessions_dir.exists() {
            return Ok(Vec::new());
        }
        let mut results = Vec::new();
        for entry in fs::read_dir(&sessions_dir)? {
            let entry = entry?;
            if !entry.file_type()?.is_dir() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            let path = entry.path();
            if meta_path(&path).exists() {
                if let Some(record) = self.load_session_meta(&name)? {
                    results.push(record);
                }
            } else {
                for sid_entry in fs::read_dir(&path)? {
                    let sid_entry = sid_entry?;
                    if !sid_entry.file_type()?.is_dir() {
                        continue;
                    }
                    let sid = sid_entry.file_name().to_string_lossy().to_string();
                    if let Some(record) = self.load_session_meta(&sid)? {
                        results.push(record);
                    }
                }
            }
        }
        results.sort_by_key(|r| r.created_at_ms);
        Ok(results)
    }

    fn delete_session(&self, session_id: &str) -> Result<bool, PersistenceError> {
        let dir = session_dir(&self.root, session_id);
        if !dir.exists() {
            return Ok(false);
        }
        fs::remove_dir_all(&dir)?;
        Ok(true)
    }

    fn append_message(&self, msg: &MessageRecord) -> Result<(), PersistenceError> {
        let dir = session_dir(&self.root, &msg.session_id);
        fs::create_dir_all(&dir)?;
        let json = serde_json::to_string(msg)?;
        append_jsonl_line(&messages_path(&dir), &json)?;
        if let Ok(Some(mut meta)) = self.load_session_meta(&msg.session_id) {
            meta.updated_at_ms = msg.timestamp_ms;
            let json = serde_json::to_string(&meta)?;
            atomic_write(&meta_path(&dir), &json)?;
        }
        Ok(())
    }

    fn load_messages(&self, session_id: &str) -> Result<Vec<MessageRecord>, PersistenceError> {
        let dir = session_dir(&self.root, session_id);
        let lines = read_jsonl_lines(&messages_path(&dir))?;
        let mut messages = Vec::with_capacity(lines.len());
        for line in &lines {
            let msg: MessageRecord = serde_json::from_str(line)?;
            messages.push(msg);
        }
        Ok(messages)
    }

    fn load_messages_from_step(
        &self,
        session_id: &str,
        step_index: usize,
    ) -> Result<Vec<MessageRecord>, PersistenceError> {
        let all = self.load_messages(session_id)?;
        Ok(all.into_iter().filter(|m| m.step_index >= step_index).collect())
    }

    fn message_count(&self, session_id: &str) -> Result<usize, PersistenceError> {
        let dir = session_dir(&self.root, session_id);
        let path = messages_path(&dir);
        if !path.exists() {
            return Ok(0);
        }
        let content = fs::read_to_string(&path)?;
        Ok(content.lines().filter(|l| !l.is_empty()).count())
    }

    fn save_checkpoint(&self, checkpoint: &Checkpoint) -> Result<(), PersistenceError> {
        let dir = session_dir(&self.root, &checkpoint.session_id);
        fs::create_dir_all(&dir)?;
        let json = serde_json::to_string(checkpoint)?;
        append_jsonl_line(&checkpoints_path(&dir), &json)?;
        Ok(())
    }

    fn load_latest_checkpoint(
        &self,
        session_id: &str,
    ) -> Result<Option<Checkpoint>, PersistenceError> {
        let checkpoints = self.list_checkpoints(session_id)?;
        Ok(checkpoints.into_iter().last())
    }

    fn load_checkpoint_at_step(
        &self,
        session_id: &str,
        step_index: usize,
    ) -> Result<Option<Checkpoint>, PersistenceError> {
        let checkpoints = self.list_checkpoints(session_id)?;
        Ok(checkpoints.into_iter().find(|c| c.step_index == step_index))
    }

    fn list_checkpoints(&self, session_id: &str) -> Result<Vec<Checkpoint>, PersistenceError> {
        let dir = session_dir(&self.root, session_id);
        let lines = read_jsonl_lines(&checkpoints_path(&dir))?;
        let mut checkpoints = Vec::with_capacity(lines.len());
        for line in &lines {
            let cp: Checkpoint = serde_json::from_str(line)?;
            checkpoints.push(cp);
        }
        Ok(checkpoints)
    }

    fn delete_checkpoints(&self, session_id: &str) -> Result<(), PersistenceError> {
        let dir = session_dir(&self.root, session_id);
        let path = checkpoints_path(&dir);
        if path.exists() {
            fs::remove_file(&path)?;
        }
        Ok(())
    }

    fn save_prompt_history(&self, record: &PromptHistoryRecord) -> Result<(), PersistenceError> {
        let dir = session_dir(&self.root, &record.session_id);
        fs::create_dir_all(&dir)?;
        let json = serde_json::to_string(record)?;
        append_jsonl_line(&prompt_history_path(&dir), &json)?;
        Ok(())
    }

    fn load_prompt_history(
        &self,
        session_id: &str,
    ) -> Result<Vec<PromptHistoryRecord>, PersistenceError> {
        let dir = session_dir(&self.root, session_id);
        let lines = read_jsonl_lines(&prompt_history_path(&dir))?;
        let mut history = Vec::with_capacity(lines.len());
        for line in &lines {
            let r: PromptHistoryRecord = serde_json::from_str(line)?;
            history.push(r);
        }
        Ok(history)
    }

    fn save_compaction(&self, record: &CompactionRecord) -> Result<(), PersistenceError> {
        let dir = session_dir(&self.root, &record.session_id);
        fs::create_dir_all(&dir)?;
        let json = serde_json::to_string(record)?;
        atomic_write(&compaction_path(&dir), &json)?;
        Ok(())
    }

    fn load_compaction(
        &self,
        session_id: &str,
    ) -> Result<Option<CompactionRecord>, PersistenceError> {
        let dir = session_dir(&self.root, session_id);
        let path = compaction_path(&dir);
        if !path.exists() {
            return Ok(None);
        }
        let content = fs::read_to_string(&path)?;
        let record: CompactionRecord = serde_json::from_str(&content)?;
        Ok(Some(record))
    }

    fn flush(&self) -> Result<(), PersistenceError> {
        Ok(())
    }
}