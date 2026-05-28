import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useUser } from '../context/UserContext';
import type { LLMConfig } from '../types';

interface Preset {
  provider: string;
  base_url: string;
  model: string;
  label: string;
}

const PRESETS: Preset[] = [
  { provider: 'anthropic', base_url: 'https://api.anthropic.com', model: 'claude-sonnet-4-20250514', label: 'Anthropic' },
  { provider: 'openai', base_url: 'https://api.openai.com/v1', model: 'gpt-4o', label: 'OpenAI' },
  { provider: 'dashscope', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', label: 'DashScope' },
  { provider: 'xai', base_url: 'https://api.x.ai/v1', model: 'grok-3-mini-fast-beta', label: 'xAI' },
  { provider: 'custom', base_url: '', model: '', label: 'Custom' },
];

const PRESET_DEFAULTS: Record<string, { base_url: string; model: string }> = {
  anthropic: { base_url: 'https://api.anthropic.com', model: 'claude-sonnet-4-20250514' },
  openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-4o' },
  dashscope: { base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  xai: { base_url: 'https://api.x.ai/v1', model: 'grok-3-mini-fast-beta' },
  custom: { base_url: '', model: '' },
};

const CONTAINER: React.CSSProperties = {
  maxWidth: 640, margin: '32px auto', padding: '0 24px', width: '100%',
};
const CARD: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border)',
  borderRadius: 12, padding: 24,
};
const FIELD_LABEL: React.CSSProperties = {
  display: 'block', fontSize: 13, color: '#aaa', marginBottom: 6,
};
const FIELD_INPUT: React.CSSProperties = {
  width: '100%', padding: '10px 12px', border: '1px solid var(--border)',
  borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)',
  fontSize: 14, outline: 'none',
};

export default function Settings() {
  const navigate = useNavigate();
  const { user } = useUser();
  const [provider, setProvider] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [systemPromptRole, setSystemPromptRole] = useState('You are a database diagnosis assistant.');
  const [systemPromptBackground, setSystemPromptBackground] = useState('');
  const [systemPromptRules, setSystemPromptRules] = useState('');
  const [previewText, setPreviewText] = useState('');
  const [showPreview, setShowPreview] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [status, setStatus] = useState('');
  const [statusOk, setStatusOk] = useState(false);
  const [activePreset, setActivePreset] = useState('');

  useEffect(() => {
    if (!user) return;
    api<LLMConfig>('/api/config')
      .then((data) => {
        if (data.provider) setProvider(data.provider);
        if (data.base_url) setBaseUrl(data.base_url);
        if (data.model) setModel(data.model);
        if (data.system_prompt_role) setSystemPromptRole(data.system_prompt_role);
        if (data.system_prompt_background) setSystemPromptBackground(data.system_prompt_background);
        if (data.system_prompt_rules) setSystemPromptRules(data.system_prompt_rules);
        if (data.api_key) setApiKey(data.api_key);
        if (data.has_api_key) setStatus('Previous configuration loaded');
      })
      .catch(() => {});
  }, [user]);

  const selectPreset = (p: Preset) => {
    setProvider(p.provider);
    setBaseUrl(p.base_url);
    setModel(p.model);
    setActivePreset(p.provider);
  };

  const handleProviderChange = (val: string) => {
    setProvider(val);
    setActivePreset('');
    if (PRESET_DEFAULTS[val]) {
      setBaseUrl(PRESET_DEFAULTS[val].base_url);
      setModel(PRESET_DEFAULTS[val].model);
    }
  };

  const currentConfigBody = () => ({
    provider,
    api_key: apiKey,
    base_url: baseUrl,
    model,
    system_prompt_role: systemPromptRole,
    system_prompt_background: systemPromptBackground,
    system_prompt_rules: systemPromptRules,
  });

  const save = async () => {
    if (!provider) { setStatus('Please select a provider.'); setStatusOk(false); return; }
    if (!apiKey) { setStatus('Please enter an API key.'); setStatusOk(false); return; }
    if (!model) { setStatus('Please enter a model name.'); setStatusOk(false); return; }
    try {
      await api('/api/config', {
        method: 'POST',
        body: currentConfigBody(),
      });
      setStatus('Configuration saved! Redirecting to chat...');
      setStatusOk(true);
      setTimeout(() => navigate('/chat'), 800);
    } catch (e) {
      setStatus(`Error: ${e instanceof Error ? e.message : 'save failed'}`);
      setStatusOk(false);
    }
  };

  const previewSystemPrompt = async () => {
    try {
      await api('/api/config', {
        method: 'POST',
        body: currentConfigBody(),
      });
      const data = await api<{ assembled: string }>('/api/system_prompt/preview');
      setPreviewText(data.assembled || '(empty)');
      setShowPreview(true);
      setStatus('Preview shown');
      setStatusOk(true);
    } catch (e) {
      setStatus(`Preview failed: ${e instanceof Error ? e.message : 'error'}`);
      setStatusOk(false);
    }
  };

  const testConnection = async () => {
    if (!apiKey || !model) { setStatus('Save configuration first.'); setStatusOk(false); return; }
    setStatus('Testing connection...');
    setStatusOk(true);

    try {
      await api('/api/config', {
        method: 'POST',
        body: currentConfigBody(),
      });

      const session = await api<{ session_id: string }>('/api/sessions', { method: 'POST' });
      if (!session.session_id) {
        setStatus('Failed to create session'); setStatusOk(false);
        return;
      }

      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${location.host}/ws/chat/${session.session_id}`);

      let responded = false;
      ws.onopen = () => ws.send(JSON.stringify({ type: 'user_message', content: 'Hello, reply with a single word: working.' }));

      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'text_delta' && !responded) {
          responded = true;
          setStatus('Connection successful! LLM responded.');
          setStatusOk(true);
          ws.close();
          api(`/api/sessions/${session.session_id}`, { method: 'DELETE' }).catch(() => {});
        } else if (msg.type === 'error' && !responded) {
          responded = true;
          setStatus(`LLM error: ${msg.message}`); setStatusOk(false);
          ws.close();
          api(`/api/sessions/${session.session_id}`, { method: 'DELETE' }).catch(() => {});
        } else if (msg.type === 'turn_complete' && !responded) {
          responded = true;
          setStatus('Turn completed but no text received.'); setStatusOk(false);
          ws.close();
          api(`/api/sessions/${session.session_id}`, { method: 'DELETE' }).catch(() => {});
        }
      };
      ws.onerror = () => { if (!responded) { setStatus('WebSocket connection error.'); setStatusOk(false); } };
      setTimeout(() => {
        if (!responded) {
          setStatus('Timeout — no response within 15s.'); setStatusOk(false);
          ws.close();
          api(`/api/sessions/${session.session_id}`, { method: 'DELETE' }).catch(() => {});
        }
      }, 15000);
    } catch (e) {
      setStatus(`Error: ${e instanceof Error ? e.message : 'test failed'}`);
      setStatusOk(false);
    }
  };

  return (
    <div style={CONTAINER}>
      <div style={CARD}>
        <h2 style={{ fontSize: 16, color: 'var(--accent)', marginBottom: 20 }}>LLM Provider Configuration</h2>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          {PRESETS.map((p) => (
            <span
              key={p.provider}
              onClick={() => selectPreset(p)}
              style={{
                padding: '6px 14px', background: 'var(--bg-primary)',
                border: `1px solid ${activePreset === p.provider ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 20, fontSize: 12, cursor: 'pointer',
                color: activePreset === p.provider ? 'var(--accent)' : '#aaa',
                transition: 'all 0.15s',
              }}
            >
              {p.label}
            </span>
          ))}
        </div>

        <div style={{ marginBottom: 16 }}>
          <label style={FIELD_LABEL} htmlFor="provider">Provider</label>
          <select id="provider" value={provider} onChange={(e) => handleProviderChange(e.target.value)} style={FIELD_INPUT}>
            <option value="">-- Select --</option>
            <option value="anthropic">Anthropic (Claude)</option>
            <option value="openai">OpenAI (GPT)</option>
            <option value="dashscope">DashScope (Qwen)</option>
            <option value="xai">xAI (Grok)</option>
            <option value="custom">Custom OpenAI-Compatible</option>
          </select>
        </div>

        <div style={{ marginBottom: 16 }}>
          <label style={FIELD_LABEL} htmlFor="apiKey">API Key</label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'stretch' }}>
            <input id="apiKey" type={showKey ? 'text' : 'password'} value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={status.includes('saved') ? '(saved — enter to change)' : 'sk-...'}
              style={{ ...FIELD_INPUT, flex: 1 }} />
            <button
              onClick={() => setShowKey((v) => !v)}
              title={showKey ? '隐藏' : '显示'}
              style={{
                padding: '0 12px', border: '1px solid var(--border)', borderRadius: 8,
                background: 'var(--bg-primary)', color: 'var(--text-secondary)',
                cursor: 'pointer', fontSize: 14, lineHeight: 1, whiteSpace: 'nowrap',
              }}>
              {showKey ? '🙈' : '👁️'}
            </button>
          </div>
        </div>

        <div style={{ marginBottom: 16 }}>
          <label style={FIELD_LABEL} htmlFor="baseUrl">Base URL</label>
          <input id="baseUrl" type="text" value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.openai.com/v1" style={FIELD_INPUT} />
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            Leave empty to use provider default. For custom endpoints (Ollama, vLLM, etc.), enter the full base URL.
          </div>
        </div>

        <div style={{ marginBottom: 16 }}>
          <label style={FIELD_LABEL} htmlFor="model">Model</label>
          <input id="model" type="text" value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="e.g., gpt-4o, claude-sonnet-4-20250514" style={FIELD_INPUT} />
        </div>

        {/* Triple-segment system prompt */}
        <div style={{
          background: '#11192e', border: '1px solid var(--border)',
          borderRadius: 10, padding: '18px 18px 14px', margin: '4px 0 18px',
        }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 4 }}>
            <h3 style={{ fontSize: 14, color: '#4ecca3', fontWeight: 600, letterSpacing: '0.02em', margin: 0 }}>
              System Prompt
            </h3>
            <span style={{ fontSize: 11, color: '#6a7090', fontFamily: 'ui-monospace, monospace' }}>
              role + background + rules → joined by \n\n
            </span>
          </div>
          <p style={{ fontSize: 12, color: '#8890a8', marginBottom: 14, lineHeight: 1.5 }}>
            The three segments below are concatenated in order and sent as the
            system prompt for every new session. Leave a segment empty to drop it from the join.
          </p>

          {/* Segment 1: Role */}
          <div style={{ position: 'relative', paddingLeft: 28 }}>
            <div style={{
              position: 'absolute', left: 0, top: 4, width: 20, height: 20, borderRadius: '50%',
              background: '#0f3460', color: '#4ecca3', fontSize: 11, fontWeight: 700,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontFamily: 'ui-monospace, monospace',
            }}>1</div>
            <label style={{ display: 'block', fontSize: 13, color: '#c5c5d0', marginBottom: 6, fontWeight: 500 }}>
              角色 <span style={{ color: '#6a7090', fontWeight: 400, fontSize: 12, marginLeft: 4 }}>Role</span>
            </label>
            <textarea
              value={systemPromptRole}
              onChange={(e) => setSystemPromptRole(e.target.value)}
              style={{
                width: '100%', padding: '10px 12px', border: '1px solid var(--border)',
                borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)',
                fontSize: 14, outline: 'none', fontFamily: 'inherit', resize: 'vertical',
                minHeight: 60, boxSizing: 'border-box',
              }}
            />
            <div style={{ fontSize: 11, color: '#6a7090', marginTop: 4 }}>
              Who the assistant is. Stays roughly stable across sessions.
            </div>
          </div>

          {/* Joiner */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0 8px 28px',
            color: '#4a5070', fontFamily: 'ui-monospace, monospace', fontSize: 11,
          }}>
            <span style={{ flex: 1, height: 0, borderTop: '1px dashed #2a3450' }} />
            \n\n
            <span style={{ flex: 1, height: 0, borderTop: '1px dashed #2a3450' }} />
          </div>

          {/* Segment 2: Background */}
          <div style={{ position: 'relative', paddingLeft: 28 }}>
            <div style={{
              position: 'absolute', left: 0, top: 4, width: 20, height: 20, borderRadius: '50%',
              background: '#0f3460', color: '#4ecca3', fontSize: 11, fontWeight: 700,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontFamily: 'ui-monospace, monospace',
            }}>2</div>
            <label style={{ display: 'block', fontSize: 13, color: '#c5c5d0', marginBottom: 6, fontWeight: 500 }}>
              背景 <span style={{ color: '#6a7090', fontWeight: 400, fontSize: 12, marginLeft: 4 }}>Background</span>
            </label>
            <textarea
              value={systemPromptBackground}
              onChange={(e) => setSystemPromptBackground(e.target.value)}
              placeholder="Optional context about your environment, stack, or current investigation."
              style={{
                width: '100%', padding: '10px 12px', border: '1px solid var(--border)',
                borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)',
                fontSize: 14, outline: 'none', fontFamily: 'inherit', resize: 'vertical',
                minHeight: 60, boxSizing: 'border-box',
              }}
            />
            <div style={{ fontSize: 11, color: '#6a7090', marginTop: 4 }}>
              Domain context the assistant should assume — db engines, scale, recent incidents.
            </div>
          </div>

          {/* Joiner */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0 8px 28px',
            color: '#4a5070', fontFamily: 'ui-monospace, monospace', fontSize: 11,
          }}>
            <span style={{ flex: 1, height: 0, borderTop: '1px dashed #2a3450' }} />
            \n\n
            <span style={{ flex: 1, height: 0, borderTop: '1px dashed #2a3450' }} />
          </div>

          {/* Segment 3: Rules */}
          <div style={{ position: 'relative', paddingLeft: 28 }}>
            <div style={{
              position: 'absolute', left: 0, top: 4, width: 20, height: 20, borderRadius: '50%',
              background: '#0f3460', color: '#4ecca3', fontSize: 11, fontWeight: 700,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontFamily: 'ui-monospace, monospace',
            }}>3</div>
            <label style={{ display: 'block', fontSize: 13, color: '#c5c5d0', marginBottom: 6, fontWeight: 500 }}>
              规则 <span style={{ color: '#6a7090', fontWeight: 400, fontSize: 12, marginLeft: 4 }}>Rules</span>
            </label>
            <textarea
              value={systemPromptRules}
              onChange={(e) => setSystemPromptRules(e.target.value)}
              placeholder="Optional hard constraints: forbidden actions, response shape, escalation policy."
              style={{
                width: '100%', padding: '10px 12px', border: '1px solid var(--border)',
                borderRadius: 8, background: 'var(--bg-primary)', color: 'var(--text-primary)',
                fontSize: 14, outline: 'none', fontFamily: 'inherit', resize: 'vertical',
                minHeight: 60, boxSizing: 'border-box',
              }}
            />
            <div style={{ fontSize: 11, color: '#6a7090', marginTop: 4 }}>
              Hard rules the assistant must follow. Appended after Background.
            </div>
          </div>

          {/* Footer */}
          <div style={{
            marginTop: 14, paddingTop: 14, borderTop: '1px solid #1a2342',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap',
          }}>
            <span style={{ fontSize: 11, color: '#6a7090' }}>
              Preview shows the assembled prompt including the kernel's built-in tool-usage guidance.
            </span>
            <button onClick={previewSystemPrompt} style={{
              padding: '8px 16px', border: '1px solid var(--border)', borderRadius: 8,
              background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer',
              fontSize: 13, fontWeight: 500,
            }}>
              Preview full prompt
            </button>
          </div>

          {showPreview && (
            <pre style={{
              marginTop: 14, padding: 14, background: '#0c0c1a',
              border: '1px solid var(--border)', borderRadius: 8,
              color: '#c5c5d0', fontFamily: 'ui-monospace, monospace', fontSize: 12,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 360, overflow: 'auto',
            }}>
              {previewText}
            </pre>
          )}
        </div>

        <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
          <button onClick={save}
            style={{
              padding: '10px 24px', border: 'none', borderRadius: 8, cursor: 'pointer',
              fontSize: 14, fontWeight: 600, background: 'var(--accent)', color: '#fff',
            }}>
            Save Configuration
          </button>
          <button onClick={testConnection}
            style={{
              padding: '10px 24px', border: 'none', borderRadius: 8, cursor: 'pointer',
              fontSize: 14, fontWeight: 600, background: 'var(--border)', color: 'var(--text-primary)',
            }}>
            Test Connection
          </button>
        </div>

        {status && (
          <p style={{ marginTop: 16, fontSize: 13, color: statusOk ? 'var(--success)' : 'var(--danger)' }}>
            {status}
          </p>
        )}
      </div>
    </div>
  );
}
