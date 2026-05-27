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
  maxWidth: 600, margin: '32px auto', padding: '0 24px', width: '100%',
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
  const [systemPrompt, setSystemPrompt] = useState('You are a database diagnosis assistant.');
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
        if (data.system_prompt) setSystemPrompt(data.system_prompt);
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

  const save = async () => {
    if (!provider) { setStatus('Please select a provider.'); setStatusOk(false); return; }
    if (!apiKey) { setStatus('Please enter an API key.'); setStatusOk(false); return; }
    if (!model) { setStatus('Please enter a model name.'); setStatusOk(false); return; }
    try {
      await api('/api/config', {
        method: 'POST',
        body: { provider, api_key: apiKey, base_url: baseUrl, model, system_prompt: systemPrompt },
      });
      setStatus('Configuration saved! Redirecting to chat...');
      setStatusOk(true);
      setTimeout(() => navigate('/chat'), 800);
    } catch (e) {
      setStatus(`Error: ${e instanceof Error ? e.message : 'save failed'}`);
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
        body: { provider, api_key: apiKey, base_url: baseUrl, model, system_prompt: systemPrompt },
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

        <div style={{ marginBottom: 16 }}>
          <label style={FIELD_LABEL} htmlFor="systemPrompt">System Prompt</label>
          <textarea id="systemPrompt" value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            style={{ ...FIELD_INPUT, resize: 'vertical', minHeight: 60 }} />
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
