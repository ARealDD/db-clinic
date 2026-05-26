use std::collections::HashMap;
use std::sync::Mutex;

use crate::{
    Checkpoint, CompactionRecord, MessageRecord, PersistenceError, PromptHistoryRecord,
    SessionBackend, SessionRecord,
};

struct Inner {
    sessions: HashMap<String, SessionRecord>,
    messages: HashMap<String, Vec<MessageRecord>>,
    checkpoints: HashMap<String, Vec<Checkpoint>>,
    prompt_history: HashMap<String, Vec<PromptHistoryRecord>>,
    compaction: HashMap<String, CompactionRecord>,
}

pub struct MemoryBackend {
    inner: Mutex<Inner>,
}

impl MemoryBackend {
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(Inner {
                sessions: HashMap::new(),
                messages: HashMap::new(),
                checkpoints: HashMap::new(),
                prompt_history: HashMap::new(),
                compaction: HashMap::new(),
            }),
        }
    }
}

impl SessionBackend for MemoryBackend {
    fn save_session_meta(&self, record: &SessionRecord) -> Result<(), PersistenceError> {
        self.inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .sessions
            .insert(record.session_id.clone(), record.clone());
        Ok(())
    }

    fn load_session_meta(
        &self,
        session_id: &str,
    ) -> Result<Option<SessionRecord>, PersistenceError> {
        Ok(self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .sessions
            .get(session_id)
            .cloned())
    }

    fn list_sessions(&self) -> Result<Vec<SessionRecord>, PersistenceError> {
        let mut results: Vec<SessionRecord> = self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .sessions
            .values()
            .cloned()
            .collect();
        results.sort_by_key(|r| r.created_at_ms);
        Ok(results)
    }

    fn delete_session(&self, session_id: &str) -> Result<bool, PersistenceError> {
        Ok(self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .sessions
            .remove(session_id)
            .is_some())
    }

    fn append_message(&self, msg: &MessageRecord) -> Result<(), PersistenceError> {
        self.inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .messages
            .entry(msg.session_id.clone())
            .or_default()
            .push(msg.clone());
        Ok(())
    }

    fn load_messages(&self, session_id: &str) -> Result<Vec<MessageRecord>, PersistenceError> {
        Ok(self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .messages
            .get(session_id)
            .cloned()
            .unwrap_or_default())
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
        Ok(self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .messages
            .get(session_id)
            .map_or(0, Vec::len))
    }

    fn save_checkpoint(&self, checkpoint: &Checkpoint) -> Result<(), PersistenceError> {
        self.inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .checkpoints
            .entry(checkpoint.session_id.clone())
            .or_default()
            .push(checkpoint.clone());
        Ok(())
    }

    fn load_latest_checkpoint(
        &self,
        session_id: &str,
    ) -> Result<Option<Checkpoint>, PersistenceError> {
        Ok(self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .checkpoints
            .get(session_id)
            .and_then(|v| v.last())
            .cloned())
    }

    fn load_checkpoint_at_step(
        &self,
        session_id: &str,
        step_index: usize,
    ) -> Result<Option<Checkpoint>, PersistenceError> {
        Ok(self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .checkpoints
            .get(session_id)
            .and_then(|v| v.iter().find(|c| c.step_index == step_index))
            .cloned())
    }

    fn list_checkpoints(&self, session_id: &str) -> Result<Vec<Checkpoint>, PersistenceError> {
        Ok(self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .checkpoints
            .get(session_id)
            .cloned()
            .unwrap_or_default())
    }

    fn delete_checkpoints(&self, session_id: &str) -> Result<(), PersistenceError> {
        self.inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .checkpoints
            .remove(session_id);
        Ok(())
    }

    fn save_prompt_history(&self, record: &PromptHistoryRecord) -> Result<(), PersistenceError> {
        self.inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .prompt_history
            .entry(record.session_id.clone())
            .or_default()
            .push(record.clone());
        Ok(())
    }

    fn load_prompt_history(
        &self,
        session_id: &str,
    ) -> Result<Vec<PromptHistoryRecord>, PersistenceError> {
        Ok(self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .prompt_history
            .get(session_id)
            .cloned()
            .unwrap_or_default())
    }

    fn save_compaction(&self, record: &CompactionRecord) -> Result<(), PersistenceError> {
        self.inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .compaction
            .insert(record.session_id.clone(), record.clone());
        Ok(())
    }

    fn load_compaction(
        &self,
        session_id: &str,
    ) -> Result<Option<CompactionRecord>, PersistenceError> {
        Ok(self
            .inner
            .lock()
            .map_err(|e| PersistenceError::Config(e.to_string()))?
            .compaction
            .get(session_id)
            .cloned())
    }

    fn flush(&self) -> Result<(), PersistenceError> {
        Ok(())
    }
}