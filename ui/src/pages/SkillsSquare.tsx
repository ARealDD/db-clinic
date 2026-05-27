import { useState, useEffect } from 'react';
import { api } from '../api';
import { useUser } from '../context/UserContext';
import SkillCard from '../components/SkillCard';
import SkillDetailModal from '../components/SkillDetailModal';
import type { Skill, SkillSquareData } from '../types';

const CONTAINER: React.CSSProperties = {
  flex: 1, maxWidth: 1200, width: '100%', margin: '0 auto', padding: 24,
};
const SECTION_TITLE: React.CSSProperties = {
  fontSize: 18, fontWeight: 700, marginBottom: 16, color: 'var(--accent)',
};
const GRID: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16,
};

export default function SkillsSquare() {
  const { user } = useUser();
  const [official, setOfficial] = useState<Skill[]>([]);
  const [published, setPublished] = useState<Skill[]>([]);
  const [tab, setTab] = useState<'official' | 'published'>('official');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);

  useEffect(() => {
    loadSquare();
  }, [search, category]);

  const loadSquare = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (search) params.set('search', search);
      if (category) params.set('category', category);
      const data = await api<SkillSquareData>(`/api/skills/square?${params}`);
      setOfficial(data.official);
      setPublished(data.published);
    } catch (e) {
      setMessage(`Failed to load: ${e instanceof Error ? e.message : 'error'}`);
    } finally {
      setLoading(false);
    }
  };

  const handleClone = async (skill: Skill) => {
    if (!user) return;
    try {
      await api('/api/skills/clone', { method: 'POST', body: { skill_id: skill.id, user_id: user.id } });
      setMessage(`"${skill.name}" added to your repository!`);
      setTimeout(() => setMessage(''), 3000);
    } catch (e) {
      setMessage(`Failed: ${e instanceof Error ? e.message : 'error'}`);
    }
  };

  const categories = [...new Set([...official, ...published].map(s => {
    try { return JSON.parse(s.metadata || '{}').category as string; } catch { return ''; }
  }).filter(Boolean))];

  return (
    <div style={CONTAINER}>
      <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 8 }}>技能广场</h1>
      <p style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 24 }}>
        浏览官方和社区发布的诊断技能，添加到你的仓库中使用
      </p>

      <div style={{ display: 'flex', gap: 12, marginBottom: 24, flexWrap: 'wrap' }}>
        <input
          type="text" placeholder="搜索技能..." value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            flex: 1, minWidth: 200, padding: '10px 14px', borderRadius: 8,
            border: '1px solid var(--border)', background: 'var(--bg-secondary)',
            color: 'var(--text-primary)', fontSize: 14, outline: 'none',
          }}
        />
        <select
          value={category} onChange={(e) => setCategory(e.target.value)}
          style={{
            padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)',
            background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14,
          }}>
          <option value="">所有分类</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {message && (
        <div style={{
          padding: '10px 16px', borderRadius: 8, marginBottom: 16,
          background: message.includes('Failed') ? 'rgba(233,69,96,0.1)' : 'rgba(35,134,54,0.1)',
          color: message.includes('Failed') ? 'var(--danger)' : 'var(--success)',
          fontSize: 13,
        }}>
          {message}
        </div>
      )}

      {loading ? (
        <p style={{ color: 'var(--text-secondary)' }}>加载中...</p>
      ) : (
        <>
          {/* Tab toggle */}
          <div style={{ display: 'flex', gap: 0, marginBottom: 20, borderBottom: '1px solid var(--border)' }}>
            <button
              onClick={() => setTab('official')}
              style={{
                padding: '10px 24px', border: 'none', cursor: 'pointer',
                fontSize: 14, fontWeight: 600, background: 'transparent',
                color: tab === 'official' ? 'var(--accent)' : 'var(--text-secondary)',
                borderBottom: tab === 'official' ? '2px solid var(--accent)' : '2px solid transparent',
                transition: 'all 0.15s',
              }}
            >
              官方技能 ({official.length})
            </button>
            <button
              onClick={() => setTab('published')}
              style={{
                padding: '10px 24px', border: 'none', cursor: 'pointer',
                fontSize: 14, fontWeight: 600, background: 'transparent',
                color: tab === 'published' ? 'var(--accent)' : 'var(--text-secondary)',
                borderBottom: tab === 'published' ? '2px solid var(--accent)' : '2px solid transparent',
                transition: 'all 0.15s',
              }}
            >
              社区发布 ({published.length})
            </button>
          </div>

          {tab === 'official' && (
            <div style={{ marginBottom: 32 }}>
              <h2 style={SECTION_TITLE}>官方技能 ({official.length})</h2>
              {official.length === 0 ? (
                <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>暂无官方技能</p>
              ) : (
                <div style={GRID}>
                  {official.map((s) => (
                    <SkillCard key={s.id} skill={s} variant="square" loggedIn={!!user} onClick={setSelectedSkill} onClone={handleClone} />
                  ))}
                </div>
              )}
            </div>
          )}

          {tab === 'published' && (
            <div>
              <h2 style={SECTION_TITLE}>社区发布 ({published.length})</h2>
              {published.length === 0 ? (
                <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>暂无社区发布</p>
              ) : (
                <div style={GRID}>
                  {published.map((s) => (
                    <SkillCard key={s.id} skill={s} variant="square" loggedIn={!!user} onClick={setSelectedSkill}
                      onClone={user && s.owner_id === user.id ? undefined : handleClone} />
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      <SkillDetailModal
        skill={selectedSkill}
        open={!!selectedSkill}
        onClose={() => setSelectedSkill(null)}
        loggedIn={!!user}
        onClone={handleClone}
      />
    </div>
  );
}
