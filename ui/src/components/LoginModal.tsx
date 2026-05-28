import { useState, useRef, useEffect } from 'react';
import { useUser } from '../context/UserContext';

const INPUT: React.CSSProperties = {
  width: '100%', padding: 10, borderRadius: 6, border: '1px solid #2d3139',
  background: '#0d1117', color: '#e0e0e0', fontSize: 14, boxSizing: 'border-box',
  outline: 'none',
};

interface LoginModalProps {
  open: boolean;
  onClose: () => void;
}

export default function LoginModal({ open, onClose }: LoginModalProps) {
  const { login, register } = useUser();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setUsername('');
      setPassword('');
      setConfirmPassword('');
      setShowPassword(false);
      setShowConfirmPassword(false);
      setError('');
      setMode('login');
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  const handleSubmit = async () => {
    const trimmed = username.trim();
    if (!trimmed || !password) return;
    if (trimmed.length < 3) { setError('用户名至少需要 3 个字符'); return; }
    if (password.length < 6) { setError('密码至少需要 6 个字符'); return; }
    if (mode === 'register' && password !== confirmPassword) {
      setError('两次输入的密码不一致');
      return;
    }
    setLoading(true);
    setError('');
    try {
      if (mode === 'register') {
        await register(trimmed, password);
      } else {
        await login(trimmed, password);
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
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

        <div style={{ display: 'flex', gap: 0, marginBottom: 16, borderBottom: '1px solid #2d3139' }}>
          <button
            onClick={() => { setMode('login'); setShowPassword(false); }}
            style={{
              flex: 1, padding: '8px 0', border: 'none', cursor: 'pointer',
              fontSize: 14, fontWeight: 600, background: 'transparent',
              color: mode === 'login' ? 'var(--accent)' : '#aaa',
              borderBottom: mode === 'login' ? '2px solid var(--accent)' : '2px solid transparent',
            }}
          >
            登录
          </button>
          <button
            onClick={() => setMode('register')}
            style={{
              flex: 1, padding: '8px 0', border: 'none', cursor: 'pointer',
              fontSize: 14, fontWeight: 600, background: 'transparent',
              color: mode === 'register' ? 'var(--accent)' : '#aaa',
              borderBottom: mode === 'register' ? '2px solid var(--accent)' : '2px solid transparent',
            }}
          >
            注册
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <input
            ref={inputRef}
            type="text"
            placeholder="用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
            style={INPUT}
          />
          <div style={{ position: 'relative' }}>
            <input
              type={showPassword ? 'text' : 'password'}
              placeholder="密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
              style={INPUT}
            />
            {password && (
              <span
                onClick={() => setShowPassword((v) => !v)}
                style={{
                  position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
                  cursor: 'pointer', fontSize: 13, color: 'var(--text-secondary)',
                  userSelect: 'none',
                }}
              >
                {showPassword ? '🙈' : '👁️'}
              </span>
            )}
          </div>
          {mode === 'register' && (
            <div style={{ position: 'relative' }}>
              <input
                type={showConfirmPassword ? 'text' : 'password'}
                placeholder="确认密码"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
                style={INPUT}
              />
              {confirmPassword && (
                <span
                  onClick={() => setShowConfirmPassword((v) => !v)}
                  style={{
                    position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
                    cursor: 'pointer', fontSize: 13, color: 'var(--text-secondary)',
                    userSelect: 'none',
                  }}
                >
                  {showConfirmPassword ? '🙈' : '👁️'}
                </span>
              )}
            </div>
          )}
        </div>

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
            onClick={handleSubmit}
            disabled={loading || !username.trim() || !password}
            style={{
              padding: '8px 16px', borderRadius: 6, border: 'none',
              background: loading ? '#555' : 'var(--success)', color: '#fff',
              cursor: loading ? 'default' : 'pointer',
              opacity: username.trim() && password ? 1 : 0.5,
            }}
          >
            {loading ? '...' : mode === 'register' ? '注册' : '确认'}
          </button>
        </div>
      </div>
    </div>
  );
}
