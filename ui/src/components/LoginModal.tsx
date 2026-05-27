import { useState, useRef, useEffect } from 'react';
import { useUser } from '../context/UserContext';

interface LoginModalProps {
  open: boolean;
  onClose: () => void;
}

export default function LoginModal({ open, onClose }: LoginModalProps) {
  const { login } = useUser();
  const [username, setUsername] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setUsername('');
      setError('');
      // Focus input after modal opens
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  const handleLogin = async () => {
    const trimmed = username.trim();
    if (!trimmed) return;
    setLoading(true);
    setError('');
    try {
      await login(trimmed);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : '登录失败');
    } finally {
      setLoading(false);
    }
  };

  if (!open) return null;

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        background: 'rgba(0,0,0,0.5)', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: '#1a1d23', padding: 32, borderRadius: 12, width: 360,
          border: '1px solid #2d3139', color: '#e0e0e0',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 style={{ margin: '0 0 16px', fontSize: 18 }}>登录 / 切换用户</h2>
        <input
          ref={inputRef}
          type="text"
          placeholder="输入用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleLogin(); }}
          style={{
            width: '100%', padding: 10, borderRadius: 6, border: '1px solid #2d3139',
            background: '#0d1117', color: '#e0e0e0', fontSize: 14, boxSizing: 'border-box',
            outline: 'none',
          }}
        />
        {error && <p style={{ color: 'var(--danger)', fontSize: 13, marginTop: 8 }}>{error}</p>}
        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
          <button
            onClick={onClose}
            style={{
              padding: '8px 16px', borderRadius: 6, border: '1px solid #2d3139',
              background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer',
            }}
          >
            取消
          </button>
          <button
            onClick={handleLogin}
            disabled={loading || !username.trim()}
            style={{
              padding: '8px 16px', borderRadius: 6, border: 'none',
              background: loading ? '#555' : 'var(--success)', color: '#fff',
              cursor: loading ? 'default' : 'pointer', opacity: username.trim() ? 1 : 0.5,
            }}
          >
            {loading ? '登录中...' : '确认'}
          </button>
        </div>
      </div>
    </div>
  );
}
