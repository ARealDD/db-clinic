import { useState, useEffect, useReducer, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useWebSocket } from '../hooks/useWebSocket';
import ChatMessage from '../components/ChatMessage';
import ProxyCard from '../components/ProxyCard';
import ToolSidebar, { type ToolEntryData } from '../components/ToolSidebar';
import SkillSidebar from '../components/SkillSidebar';
import SkillSelectorModal from '../components/SkillSelectorModal';
import SkillPickerModal from '../components/SkillPickerModal';
import SessionSidebar from '../components/SessionSidebar';
import type { LLMConfig, WsServerMessage, Session } from '../types';

// ---------- State ----------

interface MatchedSkill {
  id: string;
  name: string;
  category: string;
  score: number;
  type: string;
}

interface ChatState {
  messages: Array<{ id: string; role: 'user' | 'assistant' | 'system'; content: string; isStreaming?: boolean }>;
  proxyInstructions: Array<{
    instructionId: string;
    toolUseId: string;
    toolName: string;
    command: string;
    purpose: string;
    hint: string;
    targetEnvironment: string;
    timeoutSeconds: number;
    readOnly: boolean;
    status: 'active' | 'submitted' | 'expired' | 'archived';
  }>;
  toolEntries: ToolEntryData[];
  matchedSkills: MatchedSkill[];
  currentAssistantId: string | null;
  isThinking: boolean;
  thinkingText: string;
  inputDisabled: boolean;
  configChecked: boolean;
  hasApiKey: boolean;
  usage: string;
  skillCount: number;
}

type Action =
  | { type: 'text_delta'; content: string }
  | { type: 'thinking_delta'; content: string }
  | { type: 'skill_match'; skills: MatchedSkill[] }
  | { type: 'tool_execution'; payload: ToolEntryData }
  | { type: 'proxy_instruction'; payload: ChatState['proxyInstructions'][0] }
  | { type: 'proxy_instruction_expired'; instructionId: string }
  | { type: 'turn_complete'; usage: { input_tokens: number; output_tokens: number } }
  | { type: 'error'; message: string; recoverable: boolean }
  | { type: 'config_checked'; hasApiKey: boolean }
  | { type: 'add_user_message'; content: string }
  | { type: 'set_input_enabled' }
  | { type: 'set_messages'; messages: ChatState['messages'] }
  | { type: 'clear_chat' }
  | { type: 'expire_all_proxy_instructions' }
  | { type: 'restore_state'; state: ChatState };

let msgCounter = 0;
const newId = () => `msg-${++msgCounter}`;

function chatReducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case 'config_checked':
      return { ...state, configChecked: true, hasApiKey: action.hasApiKey };

    case 'add_user_message':
      return {
        ...state,
        messages: [...state.messages, { id: newId(), role: 'user', content: action.content }],
        inputDisabled: true,
        skillCount: 0,
      };

    case 'text_delta': {
      const msgs = [...state.messages];
      const existingIdx = msgs.findIndex((m) => m.id === state.currentAssistantId);
      if (existingIdx >= 0) {
        msgs[existingIdx] = { ...msgs[existingIdx], content: msgs[existingIdx].content + action.content, isStreaming: true };
      } else {
        const id = newId();
        return { ...state, messages: [...msgs, { id, role: 'assistant', content: action.content, isStreaming: true }], currentAssistantId: id, isThinking: false };
      }
      return { ...state, messages: msgs, isThinking: false };
    }

    case 'thinking_delta':
      return { ...state, isThinking: true, thinkingText: state.thinkingText + action.content };

    case 'skill_match':
      return {
        ...state,
        matchedSkills: action.skills,
        skillCount: action.skills.length,
      };

    case 'tool_execution': {
      const idx = state.toolEntries.findIndex((e) => e.toolUseId === action.payload.toolUseId);
      if (idx >= 0) {
        // Upgrade an existing entry (e.g. started → completed/failed) instead
        // of duplicating it. The live ToolUse event arrives first as
        // status="started" with empty output; the post-turn finished event
        // overwrites with the real status + output.
        const next = [...state.toolEntries];
        next[idx] = {
          ...next[idx],
          status: action.payload.status,
          output: action.payload.output || next[idx].output,
          isError: action.payload.isError,
          toolName: action.payload.toolName || next[idx].toolName,
        };
        return { ...state, toolEntries: next };
      }
      return { ...state, toolEntries: [...state.toolEntries, action.payload] };
    }

    case 'proxy_instruction': {
      const existingIdx = state.proxyInstructions.findIndex(
        (pi) => pi.instructionId === action.payload.instructionId
      );
      if (existingIdx >= 0) {
        const updated = [...state.proxyInstructions];
        updated[existingIdx] = action.payload;
        return { ...state, proxyInstructions: updated };
      }
      return {
        ...state,
        proxyInstructions: [...state.proxyInstructions, action.payload],
        messages: [...state.messages, { id: newId(), role: 'system', content: `🔧 **需要执行:** ${action.payload.toolName} — ${action.payload.purpose || action.payload.command}` }],
      };
    }

    case 'proxy_instruction_expired':
      return {
        ...state,
        proxyInstructions: state.proxyInstructions.map((pi) =>
          pi.instructionId === action.instructionId ? { ...pi, status: 'expired' as const } : pi
        ),
      };

    case 'turn_complete':
      return {
        ...state,
        currentAssistantId: null,
        isThinking: false,
        thinkingText: '',
        inputDisabled: false,
        messages: state.messages.map((m) => m.isStreaming ? { ...m, isStreaming: false } : m),
        usage: `Tokens: ${action.usage.input_tokens} in / ${action.usage.output_tokens} out`,
      };

    case 'error':
      return {
        ...state,
        inputDisabled: !action.recoverable,
        messages: [...state.messages, { id: newId(), role: 'system', content: `❌ **Error:** ${action.message}` }],
      };

    case 'expire_all_proxy_instructions':
      return {
        ...state,
        proxyInstructions: state.proxyInstructions.map((pi) =>
          pi.status === 'active' ? { ...pi, status: 'expired' as const } : pi
        ),
      };

    case 'set_input_enabled':
      return { ...state, inputDisabled: false };

    case 'set_messages':
      return { ...state, messages: action.messages };

    case 'restore_state':
      return action.state;

    case 'clear_chat':
      return {
        ...state,
        messages: [],
        proxyInstructions: [],
        toolEntries: [],
        matchedSkills: [],
        currentAssistantId: null,
        isThinking: false,
        thinkingText: '',
        inputDisabled: false,
        usage: '',
      };

    default:
      return state;
  }
}

const initialState: ChatState = {
  messages: [],
  proxyInstructions: [],
  toolEntries: [],
  matchedSkills: [],
  currentAssistantId: null,
  isThinking: false,
  thinkingText: '',
  inputDisabled: false,
  configChecked: false,
  hasApiKey: false,
  usage: '',
  skillCount: 0,
};

// ---------- Component ----------

const inputStyle: React.CSSProperties = {
  flex: 1, padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)',
  background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14,
  outline: 'none', resize: 'none', fontFamily: 'inherit', maxHeight: 120,
};

export default function Chat() {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [status, setStatus] = useState<'initializing' | 'no-config' | 'ready' | 'creating' | 'resuming' | 'connecting' | 'connected' | 'error'>('initializing');
  const [toolSidebarVisible, setToolSidebarVisible] = useState(false);
  const [skillSidebarVisible, setSkillSidebarVisible] = useState(false);
  const [skillSelectorOpen, setSkillSelectorOpen] = useState(false);
  const [skillCount, setSkillCountLocal] = useState(0);
  const [pendingSkills, setPendingSkills] = useState<MatchedSkill[] | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const loadingRef = useRef(false);
  // Per-session ChatState snapshots, keyed by session_id. We restore from here
  // on switch-back so an in-flight turn (especially a pending proxy card) keeps
  // its UI alive — the kernel-side resume is a no-op for live sessions
  // (session_store.rs), so the React-side cache is the source of truth until
  // the next turn checkpoints to disk.
  const sessionStatesRef = useRef<Map<string, ChatState>>(new Map());
  const stateRef = useRef<ChatState>(state);
  const sessionIdRef = useRef<string | null>(sessionId);
  useEffect(() => { stateRef.current = state; }, [state]);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);
  // Keep the per-session snapshot in lock-step with current state so the
  // restore-on-switch path always has the latest data. Skip persisting the
  // *initial* empty state for sessions we haven't actually opened yet.
  useEffect(() => {
    if (sessionId) sessionStatesRef.current.set(sessionId, state);
  }, [state, sessionId]);

  const ws = useWebSocket(sessionId);

  // Check API config on mount
  useEffect(() => {
    api<LLMConfig>('/api/config')
      .then((cfg) => {
        dispatch({ type: 'config_checked', hasApiKey: cfg.has_api_key });
        if (!cfg.has_api_key) {
          setStatus('no-config');
        } else {
          setStatus('ready');
        }
      })
      .catch(() => {
        dispatch({ type: 'config_checked', hasApiKey: false });
        setStatus('error');
      });
  }, []);

  // Load skill count
  const loadSkillCount = useCallback(() => {
    api<{ skills: unknown[]; active_ids: string[] }>('/api/skills/mine')
      .then((data) => setSkillCountLocal(data.active_ids.length))
      .catch(() => {});
  }, []);

  useEffect(() => { loadSkillCount(); }, [loadSkillCount]);

  // When 'ready', load sessions and initialize
  useEffect(() => {
    if (status !== 'ready' || loadingRef.current) return;
    loadingRef.current = true;
    loadSessionsAndInit();
  }, [status]);

  // Connect WebSocket when sessionId changes
  useEffect(() => {
    if (!sessionId) return;
    setStatus('connecting');
    ws.setOnMessage(handleWsMessage);
    ws.connect();
  }, [sessionId]);

  // Track WS status changes
  useEffect(() => {
    if (ws.status === 'connected') setStatus('connected');
    else if (ws.status === 'error') setStatus('error');
  }, [ws.status]);

  // Scroll to bottom on new messages or proxy instructions
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [state.messages, state.proxyInstructions]);

  // ---------- Session management ----------

  const loadSessionsAndInit = async () => {
    try {
      const sessionList = await api<Session[]>('/api/sessions');
      setSessions(sessionList);
      if (sessionList.length > 0) {
        await resumeSession(sessionList[0].session_id, sessionList);
      } else {
        await createSession();
      }
    } catch (e) {
      setStatus('error');
      dispatch({ type: 'error', message: 'Failed to load sessions', recoverable: true });
    }
  };

  const resumeSession = async (sid: string, sessionList: Session[]) => {
    setStatus('resuming');
    // Prefer the in-memory snapshot. The kernel's /resume is idempotent for
    // live sessions, so we don't need to refetch history if we already have
    // the latest state in hand. This is what preserves an active proxy card
    // (and any partial assistant text / tool entries) across a session switch.
    const cached = sessionStatesRef.current.get(sid);
    if (cached) {
      try {
        await api(`/api/sessions/${sid}/resume`, { method: 'POST' });
      } catch (e) {
        // Kernel lost the session (e.g. restarted): fall through to history
        // reload below.
        console.warn('resume failed, falling back to history reload:', e);
        sessionStatesRef.current.delete(sid);
      }
      if (sessionStatesRef.current.has(sid)) {
        dispatch({ type: 'restore_state', state: cached });
        setSessionId(sid);
        setSessions(sessionList);
        return;
      }
    }

    dispatch({ type: 'clear_chat' });
    try {
      await api(`/api/sessions/${sid}/resume`, { method: 'POST' });
    } catch (e) {
      // Session may have expired — create a new one
      console.warn('resume failed, creating new session:', e);
      await createSession();
      return;
    }
    // Load history
    try {
      const historyData = await api<{ messages: Array<{ role: string; content: string }> }>(`/api/sessions/${sid}/history`);
      if (historyData.messages && historyData.messages.length > 0) {
        const msgs = historyData.messages.map((m) => ({
          id: newId(),
          role: (m.role === 'user' || m.role === 'assistant' || m.role === 'system' ? m.role : 'user') as 'user' | 'assistant' | 'system',
          content: m.content,
        }));
        dispatch({ type: 'set_messages', messages: msgs });
      }
    } catch (e) {
      console.warn('load history failed:', e);
    }
    setSessionId(sid);
    setSessions(sessionList);
  };

  const createSession = async () => {
    setStatus('creating');
    try {
      const data = await api<{ session_id: string; created_at_ms?: number }>('/api/sessions', { method: 'POST' });
      setSessions((prev) => {
        const updated = [{ session_id: data.session_id, created_at_ms: data.created_at_ms || 0 }, ...prev];
        return updated;
      });
      setSessionId(data.session_id);
    } catch (e) {
      setStatus('error');
      dispatch({ type: 'error', message: 'Failed to create session', recoverable: true });
    }
  };

  const handleSelectSession = async (sid: string) => {
    if (sid === sessionId || ws.status === 'connecting') return;
    ws.disconnect();
    setSessionId(null);
    await resumeSession(sid, sessions);
  };

  const handleDeleteSession = async (sid: string) => {
    try {
      await api(`/api/sessions/${sid}`, { method: 'DELETE' });
      sessionStatesRef.current.delete(sid);
      const updated = sessions.filter((s) => s.session_id !== sid);
      setSessions(updated);
      if (sid === sessionId) {
        ws.disconnect();
        setSessionId(null);
        dispatch({ type: 'clear_chat' });
        if (updated.length > 0) {
          await resumeSession(updated[0].session_id, updated);
        } else {
          await createSession();
        }
      }
    } catch (e) {
      console.warn('delete session failed:', e);
    }
  };

  const handleCreateSession = async () => {
    if (status === 'creating' || status === 'resuming') return;
    ws.disconnect();
    setSessionId(null);
    dispatch({ type: 'clear_chat' });
    await createSession();
    // Refresh session list
    try {
      const sessionList = await api<Session[]>('/api/sessions');
      setSessions(sessionList);
    } catch {}
  };

  // ---------- WS message handling ----------

  const handleWsMessage = useCallback((msg: WsServerMessage) => {
    switch (msg.type) {
      case 'text_delta':
        dispatch({ type: 'text_delta', content: msg.content });
        break;
      case 'thinking_delta':
        dispatch({ type: 'thinking_delta', content: msg.content });
        break;
      case 'skill_match':
        dispatch({ type: 'skill_match', skills: msg.skills });
        setPendingSkills(msg.skills);
        break;
      case 'tool_execution':
        dispatch({
          type: 'tool_execution',
          payload: {
            toolUseId: msg.tool_use_id,
            toolName: msg.tool_name,
            status: msg.status,
            output: msg.output,
            isError: msg.is_error,
          },
        });
        break;
      case 'proxy_instruction':
        dispatch({
          type: 'proxy_instruction',
          payload: {
            instructionId: msg.instruction_id,
            toolUseId: msg.tool_use_id,
            toolName: msg.tool_name,
            command: msg.command,
            purpose: msg.purpose,
            hint: msg.hint,
            targetEnvironment: msg.target_environment,
            timeoutSeconds: msg.timeout_seconds,
            readOnly: msg.read_only,
            status: 'active',
          },
        });
        break;
      case 'proxy_instruction_expired':
        dispatch({ type: 'proxy_instruction_expired', instructionId: msg.instruction_id });
        break;
      case 'turn_complete':
        dispatch({ type: 'turn_complete', usage: msg.usage });
        break;
      case 'error':
        dispatch({ type: 'error', message: msg.message, recoverable: msg.recoverable });
        // Expire all active proxy instructions on proxy-related errors
        // so the user knows to retry with a fresh message.
        if (msg.message.includes('proxy instruction') || msg.message.includes('pending proxy')) {
          dispatch({ type: 'expire_all_proxy_instructions' });
        }
        break;
      case 'usage_update':
        break;
    }
  }, []);

  const sendMessage = () => {
    const trimmed = input.trim();
    if (!trimmed || state.inputDisabled) return;
    setInput('');
    dispatch({ type: 'add_user_message', content: trimmed });
    ws.send({ type: 'user_message', content: trimmed });
  };

  const handleCancel = () => {
    ws.send({ type: 'cancel' });
    dispatch({ type: 'set_input_enabled' });
  };

  const handleProxyResult = (data: { instruction_id: string; tool_use_id: string; output: string; is_error: boolean }) => {
    ws.send({ type: 'proxy_result', ...data });
    const found = state.proxyInstructions.find((pi) => pi.instructionId === data.instruction_id);
    if (found) {
      dispatch({
        type: 'proxy_instruction',
        payload: { ...found, status: 'submitted' as const },
      });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const handleSkillConfirm = (ids: string[]) => {
    ws.send({ type: 'skill_selection', skill_ids: ids });
    setPendingSkills(null);
  };

  const handleSkillCancel = () => {
    ws.send({ type: 'skill_selection', skill_ids: [] });
    setPendingSkills(null);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
  };

  const statusColor = state.inputDisabled
    ? 'var(--warning)'
    : status === 'connected'
    ? 'var(--success)'
    : 'var(--text-secondary)';

  // -------- Render --------

  if (!state.configChecked) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: 'var(--text-secondary)' }}>
        Loading...
      </div>
    );
  }

  if (status === 'no-config') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100vh', gap: 16, padding: 24, textAlign: 'center' }}>
        <div style={{ fontSize: 48 }}>⚙️</div>
        <h2 style={{ fontSize: 20, color: 'var(--text-primary)' }}>No LLM Provider Configured</h2>
        <p style={{ color: 'var(--text-secondary)', maxWidth: 400, lineHeight: 1.5 }}>
          Please configure your API key and model in the Settings page before starting a diagnosis session.
        </p>
        <Link to="/settings" style={{
          padding: '10px 24px', borderRadius: 8, background: 'var(--accent)',
          color: '#fff', fontWeight: 600, textDecoration: 'none', fontSize: 14,
        }}>
          Go to Settings
        </Link>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      {/* Session sidebar */}
      <SessionSidebar
        sessions={sessions}
        activeSessionId={sessionId}
        onSelect={handleSelectSession}
        onDelete={handleDeleteSession}
        onCreate={handleCreateSession}
        loading={status === 'creating' || status === 'resuming'}
      />

      {/* Main chat area */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {/* Status bar */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px',
          borderBottom: '1px solid var(--border)', fontSize: 13, color: 'var(--text-secondary)',
          background: 'var(--bg-secondary)', flexShrink: 0,
        }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: statusColor, display: 'inline-block' }} />
          <span>
            {status === 'connected' ? 'Connected'
              : status === 'creating' ? 'Creating session...'
              : status === 'resuming' ? 'Resuming session...'
              : status === 'connecting' ? 'Connecting...'
              : 'Disconnected'}
          </span>
          {state.isThinking && <span style={{ color: 'var(--warning)', fontSize: 12 }}>Thinking...</span>}
          <span style={{ flex: 1 }} />
          <button onClick={() => setSkillSidebarVisible((v) => !v)} style={{
            background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer',
            fontSize: 13, padding: 0, textDecoration: 'underline',
          }}>
            技能 {state.skillCount || skillCount}
          </button>
          <button onClick={() => setSkillSelectorOpen(true)} disabled={state.inputDisabled} style={{
            background: 'none', border: 'none', color: state.inputDisabled ? 'var(--text-muted)' : 'var(--link)',
            cursor: state.inputDisabled ? 'not-allowed' : 'pointer',
            fontSize: 13, textDecoration: 'underline', padding: 0,
          }}>
            选择
          </button>
          {state.usage && <span style={{ color: 'var(--text-muted)', marginLeft: 8, fontSize: 12 }}>{state.usage}</span>}
          <span style={{ color: 'var(--text-muted)' }}>|</span>
          <button onClick={() => setToolSidebarVisible((v) => !v)} style={{
            background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer',
            fontSize: 13, padding: 0,
          }}>
            tools
          </button>
        </div>

        {/* Messages area */}
        <div style={{
          flex: 1, overflow: 'auto', padding: 16, display: 'flex',
          flexDirection: 'column',
          marginRight: toolSidebarVisible ? 300 : 0,
          transition: 'margin-right 0.2s',
        }}>
          {state.messages.length === 0 && (
            <div style={{ textAlign: 'center', marginTop: 64, color: 'var(--text-secondary)' }}>
              <div style={{ fontSize: 48, marginBottom: 16 }}>🏥</div>
              <h2 style={{ fontSize: 20, color: 'var(--text-primary)', marginBottom: 8 }}>DB Clinic</h2>
              <p style={{ fontSize: 14 }}>描述你的数据库问题，开始诊断</p>
            </div>
          )}

          {state.messages.map((msg) => (
            <ChatMessage key={msg.id} role={msg.role} content={msg.content} isStreaming={msg.isStreaming} />
          ))}


          <div ref={messagesEndRef} />
        </div>

        {/* Proxy card — fixed above input, always visible when active */}
        {state.proxyInstructions.filter((pi) => pi.status === 'active').length > 0 && (
          <div style={{
            marginRight: toolSidebarVisible ? 300 : 0,
            transition: 'margin-right 0.2s',
            borderTop: '1px solid var(--border)',
            background: 'var(--bg-secondary)',
            maxHeight: '40vh',
            overflow: 'auto',
          }}>
            {(() => {
              const actives = state.proxyInstructions.filter((pi) => pi.status === 'active');
              const last = actives[actives.length - 1];
              return last ? (
                <div key={last.instructionId} style={{ padding: '8px 16px' }}>
                  <ProxyCard instruction={last} onSubmitResult={handleProxyResult} />
                </div>
              ) : null;
            })()}
          </div>
        )}

        {/* Input area */}
        <div style={{
          padding: '12px 16px', borderTop: '1px solid var(--border)',
          background: 'var(--bg-secondary)', display: 'flex', gap: 8,
          alignItems: 'flex-end',
          marginRight: toolSidebarVisible ? 300 : 0,
          transition: 'margin-right 0.2s',
        }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder={state.inputDisabled ? '等待响应...' : '描述数据库问题...'}
            disabled={state.inputDisabled}
            rows={1}
            style={inputStyle}
          />
          {state.isThinking || (state.messages.length > 0 && state.inputDisabled) ? (
            <button onClick={handleCancel} style={{
              padding: '10px 16px', borderRadius: 8, border: '1px solid var(--border)',
              background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer',
              fontSize: 14, whiteSpace: 'nowrap',
            }}>
              停止
            </button>
          ) : (
            <button onClick={sendMessage} disabled={!input.trim() || state.inputDisabled} style={{
              padding: '10px 20px', borderRadius: 8, border: 'none',
              background: input.trim() && !state.inputDisabled ? 'var(--accent)' : '#555',
              color: '#fff', fontSize: 14, fontWeight: 600,
              cursor: input.trim() && !state.inputDisabled ? 'pointer' : 'default',
              whiteSpace: 'nowrap',
            }}>
              发送
            </button>
          )}
        </div>
      </div>

      {/* Skill sidebar */}
      <SkillSidebar
        skills={state.matchedSkills}
        visible={skillSidebarVisible}
        onClose={() => setSkillSidebarVisible(false)}
        onSelectSkills={() => setSkillSelectorOpen(true)}
      />

      {/* Tool sidebar */}
      <ToolSidebar
        entries={state.toolEntries}
        visible={toolSidebarVisible}
        onToggle={() => setToolSidebarVisible((v) => !v)}
      />

      {/* Skill selector (square) */}
      <SkillSelectorModal open={skillSelectorOpen} onClose={() => { setSkillSelectorOpen(false); loadSkillCount(); }} />

      {/* Skill picker (match results) */}
      <SkillPickerModal
        open={pendingSkills !== null}
        skills={pendingSkills || []}
        onConfirm={handleSkillConfirm}
        onCancel={handleSkillCancel}
      />
    </div>
  );
}
