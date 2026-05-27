import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../context/UserContext';

export default function Landing() {
  const [username, setUsername] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useUser();
  const navigate = useNavigate();

  const handleStart = async () => {
    const trimmed = username.trim();
    if (!trimmed) return;
    setLoading(true);
    setError('');
    try {
      await login(trimmed);
      navigate('/settings');
    } catch (e) {
      setError(e instanceof Error ? e.message : '登录失败');
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
      <div style={{ display: 'flex', gap: 8, maxWidth: 400, width: '100%' }}>
        <input
          type="text"
          placeholder="输入用户名开始使用"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleStart(); }}
          style={{
            flex: 1, padding: '12px 16px', borderRadius: 8, border: '1px solid var(--border)',
            background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 15,
            outline: 'none',
          }}
        />
        <button
          onClick={handleStart}
          disabled={loading || !username.trim()}
          style={{
            padding: '12px 24px', borderRadius: 8, border: 'none',
            background: loading ? '#555' : 'var(--accent)', color: '#fff',
            fontSize: 15, fontWeight: 600, cursor: loading ? 'default' : 'pointer',
            whiteSpace: 'nowrap', opacity: username.trim() ? 1 : 0.5,
          }}
        >
          {loading ? '...' : '开始'}
        </button>
      </div>
      {error && <p style={{ color: 'var(--danger)', fontSize: 13, marginTop: 12 }}>{error}</p>}
    </div>
  );
}
