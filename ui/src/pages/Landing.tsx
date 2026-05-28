import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../context/UserContext';

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '12px 16px', borderRadius: 8,
  border: '1px solid var(--border)', background: 'var(--bg-secondary)',
  color: 'var(--text-primary)', fontSize: 15, outline: 'none',
  boxSizing: 'border-box',
};

export default function Landing() {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login, register } = useUser();
  const navigate = useNavigate();

  const handleSubmit = async () => {
    const trimmed = username.trim();
    if (!trimmed || !password) return;
    if (trimmed.length < 3) {
      setError('用户名至少需要 3 个字符');
      return;
    }
    if (password.length < 6) {
      setError('密码至少需要 6 个字符');
      return;
    }
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
      navigate('/settings');
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', textAlign: 'center',
      padding: '40px 24px',
    }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>🏥</div>
      <h1 style={{ fontSize: 28, fontWeight: 700, color: 'var(--accent)', marginBottom: 8 }}>
        DB Clinic
      </h1>
      <p style={{ fontSize: 15, color: 'var(--text-secondary)', marginBottom: 32, lineHeight: 1.5 }}>
        GaussDB / openGauss 智能诊断助手<br />
        基于 LLM 的数据库性能分析与故障排查
      </p>

      <div style={{ maxWidth: 360, width: '100%' }}>
        <div style={{ display: 'flex', gap: 0, marginBottom: 20, borderBottom: '1px solid var(--border)' }}>
          <button
            onClick={() => { setMode('login'); setShowPassword(false); setShowConfirmPassword(false); }}
            style={{
              flex: 1, padding: '10px 0', border: 'none', cursor: 'pointer',
              fontSize: 14, fontWeight: 600, background: 'transparent',
              color: mode === 'login' ? 'var(--accent)' : 'var(--text-secondary)',
              borderBottom: mode === 'login' ? '2px solid var(--accent)' : '2px solid transparent',
              transition: 'all 0.15s',
            }}
          >
            登录
          </button>
          <button
            onClick={() => setMode('register')}
            style={{
              flex: 1, padding: '10px 0', border: 'none', cursor: 'pointer',
              fontSize: 14, fontWeight: 600, background: 'transparent',
              color: mode === 'register' ? 'var(--accent)' : 'var(--text-secondary)',
              borderBottom: mode === 'register' ? '2px solid var(--accent)' : '2px solid transparent',
              transition: 'all 0.15s',
            }}
          >
            注册
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <input
            type="text"
            placeholder="用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
            style={inputStyle}
          />
          <div style={{ position: 'relative' }}>
            <input
              type={showPassword ? 'text' : 'password'}
              placeholder="密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
              style={inputStyle}
            />
            {password && (
              <span
                onClick={() => setShowPassword((v) => !v)}
                style={{
                  position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
                  cursor: 'pointer', fontSize: 14, color: 'var(--text-secondary)',
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
                style={inputStyle}
              />
              {confirmPassword && (
                <span
                  onClick={() => setShowConfirmPassword((v) => !v)}
                  style={{
                    position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
                    cursor: 'pointer', fontSize: 14, color: 'var(--text-secondary)',
                    userSelect: 'none',
                  }}
                >
                  {showConfirmPassword ? '🙈' : '👁️'}
                </span>
              )}
            </div>
          )}
          <button
            onClick={handleSubmit}
            disabled={loading || !username.trim() || !password}
            style={{
              width: '100%', padding: '12px 24px', borderRadius: 8, border: 'none',
              background: loading ? '#555' : 'var(--accent)', color: '#fff',
              fontSize: 15, fontWeight: 600, cursor: loading ? 'default' : 'pointer',
              opacity: username.trim() && password ? 1 : 0.5,
            }}
          >
            {loading ? '...' : mode === 'login' ? '登录' : '注册'}
          </button>
        </div>

        {error && <p style={{ color: 'var(--danger)', fontSize: 13, marginTop: 12 }}>{error}</p>}
      </div>
    </div>
  );
}
