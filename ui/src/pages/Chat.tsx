import { useState, useEffect, useReducer, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useWebSocket } from '../hooks/useWebSocket';
import ChatMessage from '../components/ChatMessage';
import ThinkingBlock from '../components/ThinkingBlock';
import ProxyCard from '../components/ProxyCard';
import ToolSidebar, { type ToolEntryData } from '../components/ToolSidebar';
import SkillSelectorModal from '../components/SkillSelectorModal';
import type { LLMConfig, WsServerMessage } from '../types';

// ---------- State ----------

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
  | { type: 'skill_match'; skills: Array<{ id: string; name: string }> }
  | { type: 'tool_execution'; payload: ToolEntryData }
  | { type: 'proxy_instruction'; payload: ChatState['proxyInstructions'][0] }
  | { type: 'proxy_instruction_expired'; instructionId: string }
  | { type: 'turn_complete'; usage: { input_tokens: number; output_tokens: number } }
  | { type: 'error'; message: string; recoverable: boolean }
  | { type: 'config_checked'; hasApiKey: boolean }
  | { type: 'add_user_message'; content: string }
  | { type: 'set_input_enabled' };

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
        // state.currentAssistantId wasn't set — do it inline
        return { ...state, messages: [...msgs, { id, role: 'assistant', content: action.content, isStreaming: true }], currentAssistantId: id, isThinking: false };
      }
      return { ...state, messages: msgs, isThinking: false };
    }

    case 'thinking_delta':
      return { ...state, isThinking: true, thinkingText: state.thinkingText + action.content };

    case 'skill_match': {
      const skillText = `🧠 **Matched skills:** ${action.skills.map((s) => s.name).join(', ')}`;
      return {
        ...state,
        messages: [...state.messages, { id: newId(), role: 'system', content: skillText }],
        skillCount: action.skills.length,
      };
    }

    case 'tool_execution':
      return {
        ...state,
        toolEntries: [...state.toolEntries, action.payload],
      };

    case 'proxy_instruction':
      return {
        ...state,
        proxyInstructions: [...state.proxyInstructions, action.payload],
        messages: [...state.messages, { id: newId(), role: 'system', content: `🔧 **需要执行:** ${action.payload.toolName} — ${action.payload.purpose || action.payload.command}` }],
      };

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
        proxyInstructions: state.proxyInstructions.map((pi) =>
          pi.status === 'active' ? { ...pi, status: 'archived' as const } : pi
        ),
        usage: `Tokens: ${action.usage.input_tokens} in / ${action.usage.output_tokens} out`,
      };

    case 'error':
      return {
        ...state,
        inputDisabled: !action.recoverable,
        messages: [...state.messages, { id: newId(), role: 'system', content: `❌ **Error:** ${action.message}` }],
      };

    case 'set_input_enabled':
      return { ...state, inputDisabled: false };

    default:
      return state;
  }
}

const initialState: ChatState = {
  messages: [],
  proxyInstructions: [],
  toolEntries: [],
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
  const [status, setStatus] = useState<'initializing' | 'no-config' | 'ready' | 'connecting' | 'connected' | 'error'>('initializing');
  const [toolSidebarVisible, setToolSidebarVisible] = useState(false);
  const [skillSelectorOpen, setSkillSelectorOpen] = useState(false);
  const [skillCount, setSkillCountLocal] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

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
  useEffect(() => {
    api<{ skills: unknown[]; active_ids: string[] }>('/api/skills/mine')
      .then((data) => setSkillCountLocal(data.active_ids.length))
      .catch(() => {});
  }, []);

  // Create session and connect WS when ready
  useEffect(() => {
    if (status !== 'ready' || sessionId) return;
    setStatus('initializing');
    api<{ session_id: string }>('/api/sessions', {
      method: 'POST',
    })
      .then((s) => {
        setSessionId(s.session_id);
      })
      .catch(() => {
        setStatus('error');
        dispatch({ type: 'error', message: 'Failed to create session', recoverable: true });
      });
  }, [status, sessionId]);

  // Connect WebSocket when session is ready
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
    else if (ws.status === 'disconnected' && status === 'connected') {
      // was connected and now disconnected — could reconnect
    }
  }, [ws.status]);

  // Update status when WS connects
  useEffect(() => {
    if (sessionId && ws.status === 'connected') {
      setStatus('connected');
    }
  }, [sessionId, ws.status]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [state.messages]);

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
        break;
      case 'usage_update':
        // silently consumed, just like the original
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
    dispatch({
      type: 'proxy_instruction',
      payload: {
        ...state.proxyInstructions.find((pi) => pi.instructionId === data.instruction_id)!,
        status: 'submitted',
      },
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // Auto-resize textarea
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
  };

  const statusColor = state.inputDisabled ? 'var(--warning)' : status === 'connected' ? 'var(--success)' : 'var(--text-secondary)';

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
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Status bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px',
        borderBottom: '1px solid var(--border)', fontSize: 13, color: 'var(--text-secondary)',
        background: 'var(--bg-secondary)', flexShrink: 0,
      }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: statusColor, display: 'inline-block' }} />
        <span>{status === 'connected' ? 'Connected' : status === 'connecting' ? 'Connecting...' : 'Disconnected'}</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: 'var(--text-muted)' }}>技能 {state.skillCount || skillCount}</span>
        <button onClick={() => setSkillSelectorOpen(true)} style={{
          background: 'none', border: 'none', color: 'var(--link)', cursor: 'pointer',
          fontSize: 13, textDecoration: 'underline', padding: 0,
        }}>
          选择
        </button>
        <span style={{ color: 'var(--text-muted)' }}>|</span>
        <button onClick={() => setToolSidebarVisible((v) => !v)} style={{
          background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer',
          fontSize: 13, padding: 0,
        }}>
          {toolSidebarVisible ? 'Hide proxy history' : 'Show proxy history'}
        </button>
        {state.usage && <span style={{ color: 'var(--text-muted)', marginLeft: 8, fontSize: 12 }}>{state.usage}</span>}
      </div>

      {/* Messages area */}
      <div style={{
        flex: 1, overflow: 'auto', padding: 16, display: 'flex',
        flexDirection: 'column',
        marginRight: toolSidebarVisible ? 300 : 0,
        transition: 'margin-right 0.2s',
      }}>
        {state.messages.length === 0 && (
          <div style={{
            textAlign: 'center', marginTop: 64, color: 'var(--text-secondary)',
          }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>🏥</div>
            <h2 style={{ fontSize: 20, color: 'var(--text-primary)', marginBottom: 8 }}>DB Clinic</h2>
            <p style={{ fontSize: 14 }}>描述你的数据库问题，开始诊断</p>
          </div>
        )}

        {state.messages.map((msg) => (
          <ChatMessage key={msg.id} role={msg.role} content={msg.content} isStreaming={msg.isStreaming} />
        ))}

        {state.isThinking && <ThinkingBlock content={state.thinkingText} />}

        {/* Proxy instruction cards */}
        {state.proxyInstructions.filter((pi) => pi.status === 'active').map((pi) => (
          <ProxyCard key={pi.instructionId} instruction={pi} onSubmitResult={handleProxyResult} />
        ))}

        <div ref={messagesEndRef} />
      </div>

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

      {/* Tool sidebar */}
      <ToolSidebar
        entries={state.toolEntries}
        visible={toolSidebarVisible}
        onToggle={() => setToolSidebarVisible((v) => !v)}
      />

      {/* Skill selector */}
      <SkillSelectorModal open={skillSelectorOpen} onClose={() => setSkillSelectorOpen(false)} />
    </div>
  );
}
