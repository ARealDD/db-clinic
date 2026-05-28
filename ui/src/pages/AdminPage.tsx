import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useUser } from '../context/UserContext';
import type { AdminUser, AdminUsersResponse } from '../types';

const CONTAINER: React.CSSProperties = {
  flex: 1, maxWidth: 1000, width: '100%', margin: '0 auto', padding: 24,
};

export default function AdminPage() {
  const { user } = useUser();
  const navigate = useNavigate();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api<AdminUsersResponse>('/api/admin/users');
      setUsers(data.users);
    } catch (e) {
      setMessage(`Failed to load: ${e instanceof Error ? e.message : 'error'}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user?.role !== 'admin') {
      navigate('/chat', { replace: true });
      return;
    }
    loadUsers();
  }, [user, navigate, loadUsers]);

  const handleDelete = async (targetUser: AdminUser) => {
    if (targetUser.id === Number(user?.id)) return;
    if (!confirm(`确认删除用户 "${targetUser.username}"（ID: ${targetUser.id}）？\n该用户的所有技能和配置将被永久删除。`)) return;
    try {
      await api(`/api/admin/users/${targetUser.id}`, { method: 'DELETE' });
      setUsers((prev) => prev.filter((u) => u.id !== targetUser.id));
      setMessage(`用户 "${targetUser.username}" 已删除.`);
      setTimeout(() => setMessage(''), 3000);
    } catch (e) {
      setMessage(`删除失败: ${e instanceof Error ? e.message : 'error'}`);
    }
  };

  const roleBadge = (role: string) => {
    const isAdmin = role === 'admin';
    return (
      <span style={{
        display: 'inline-block', padding: '2px 8px', borderRadius: 10,
        fontSize: 11, fontWeight: 600,
        background: isAdmin ? 'rgba(88,166,255,0.15)' : 'rgba(139,148,158,0.15)',
        color: isAdmin ? 'var(--accent)' : 'var(--text-secondary)',
      }}>
        {isAdmin ? 'Admin' : 'User'}
      </span>
    );
  };

  return (
    <div style={CONTAINER}>
      <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 8 }}>用户管理</h1>
      <p style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 24 }}>
        查看和管理所有用户（管理员专用）
      </p>

      {message && (
        <div style={{
          padding: '10px 16px', borderRadius: 8, marginBottom: 16,
          background: message.includes('失败') ? 'rgba(233,69,96,0.1)' : 'rgba(35,134,54,0.1)',
          color: message.includes('失败') ? 'var(--danger)' : 'var(--success)', fontSize: 13,
        }}>
          {message}
        </div>
      )}

      {loading ? (
        <p style={{ color: 'var(--text-secondary)' }}>加载中...</p>
      ) : (
        <div style={{
          border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead>
              <tr style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
                <th style={thStyle}>ID</th>
                <th style={thStyle}>用户名</th>
                <th style={thStyle}>角色</th>
                <th style={thStyle}>技能数</th>
                <th style={thStyle}>注册时间</th>
                <th style={thStyle}>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={tdStyle}>{u.id}</td>
                  <td style={tdStyle}>{u.username}</td>
                  <td style={tdStyle}>{roleBadge(u.role)}</td>
                  <td style={tdStyle}>{u.skill_count}</td>
                  <td style={tdStyle}>{new Date(u.created_at * 1000).toLocaleString()}</td>
                  <td style={tdStyle}>
                    {u.id !== Number(user?.id) && (
                      <button
                        onClick={() => handleDelete(u)}
                        style={{
                          padding: '4px 12px', borderRadius: 4, border: '1px solid var(--danger)',
                          background: 'transparent', color: 'var(--danger)', cursor: 'pointer',
                          fontSize: 12,
                        }}
                      >
                        删除
                      </button>
                    )}
                    {u.id === Number(user?.id) && (
                      <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>当前用户</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const thStyle: React.CSSProperties = {
  padding: '10px 16px', textAlign: 'left', fontWeight: 600, fontSize: 12,
  textTransform: 'uppercase', letterSpacing: '0.05em',
};

const tdStyle: React.CSSProperties = {
  padding: '10px 16px', color: 'var(--text-primary)',
};
