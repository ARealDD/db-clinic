import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import { useUser } from '../context/UserContext';
import SkillCard from '../components/SkillCard';
import SkillEditorModal from '../components/SkillEditorModal';
import type { Skill, MySkillsData } from '../types';

const CONTAINER: React.CSSProperties = {
  flex: 1, maxWidth: 1200, width: '100%', margin: '0 auto', padding: 24,
};
const SECTION_TITLE: React.CSSProperties = {
  fontSize: 18, fontWeight: 700, marginBottom: 16, color: 'var(--accent)',
};
const GRID: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16,
};

export default function MySkills() {
  const { user } = useUser();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [activeIds, setActiveIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editSkill, setEditSkill] = useState<Skill | null>(null);
  const [message, setMessage] = useState('');

  const loadSkills = useCallback(async () => {
    if (!user) return;
    setLoading(true);
    try {
      const data = await api<MySkillsData>(`/api/skills/mine?user_id=${encodeURIComponent(user.id)}`);
      setSkills(data.skills);
      setActiveIds(data.active_ids);
    } catch (e) {
      setMessage(`Failed to load: ${e instanceof Error ? e.message : 'error'}`);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  const ownSkills = skills.filter((s) => !s.source_skill_id || s.source_is_official === 0);
  const copiedSkills = skills.filter((s) => !!s.source_skill_id && s.source_is_official === 1);

  const handleToggleActive = async (skill: Skill) => {
    if (!user) return;
    try {
      const data = await api<{ is_active: boolean }>('/api/skills/toggle-active', {
        method: 'POST', body: { skill_id: skill.id, user_id: user.id },
      });
      if (data.is_active) {
        setActiveIds((prev) => [...prev, skill.id]);
      } else {
        setActiveIds((prev) => prev.filter((id) => id !== skill.id));
      }
    } catch (e) {
      setMessage(`Failed: ${e instanceof Error ? e.message : 'error'}`);
    }
  };

  const handlePublish = async (skill: Skill) => {
    if (!user) return;
    try {
      const data = await api<{ is_published: boolean }>('/api/skills/publish', {
        method: 'POST', body: { skill_id: skill.id, user_id: user.id },
      });
      setSkills((prev) => prev.map((s) => s.id === skill.id ? { ...s, is_published: data.is_published ? 1 : 0 } : s));
    } catch (e) {
      setMessage(`Failed: ${e instanceof Error ? e.message : 'error'}`);
    }
  };

  const handleDelete = async (skill: Skill) => {
    if (!user || !confirm(`确认删除技能 "${skill.name}"？`)) return;
    try {
      await api('/api/skills', { method: 'DELETE', body: { skill_id: skill.id, user_id: user.id } });
      setSkills((prev) => prev.filter((s) => s.id !== skill.id));
      setMessage(`"${skill.name}" deleted.`);
      setTimeout(() => setMessage(''), 3000);
    } catch (e) {
      setMessage(`Failed: ${e instanceof Error ? e.message : 'error'}`);
    }
  };

  const handleEdit = (skill: Skill) => {
    setEditSkill(skill);
    setEditorOpen(true);
  };

  const handleNew = () => {
    setEditSkill(null);
    setEditorOpen(true);
  };

  const handleSaved = () => {
    loadSkills();
    setMessage('Skill saved successfully!');
    setTimeout(() => setMessage(''), 3000);
  };

  if (!user) {
    return (
      <div style={CONTAINER}>
        <p style={{ color: 'var(--text-secondary)', fontSize: 15 }}>
          请先登录以管理你的技能仓库。
        </p>
      </div>
    );
  }

  return (
    <div style={CONTAINER}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700 }}>我的仓库</h1>
          <p style={{ fontSize: 14, color: 'var(--text-secondary)', marginTop: 4 }}>
            管理你的个人技能库和克隆的技能
          </p>
        </div>
        <button onClick={handleNew} style={{
          padding: '10px 20px', border: 'none', borderRadius: 8,
          background: 'var(--accent)', color: '#fff', fontSize: 14, fontWeight: 600,
          cursor: 'pointer', whiteSpace: 'nowrap',
        }}>
          + 新建技能
        </button>
      </div>

      {message && (
        <div style={{
          padding: '10px 16px', borderRadius: 8, marginBottom: 16,
          background: message.includes('Failed') ? 'rgba(233,69,96,0.1)' : 'rgba(35,134,54,0.1)',
          color: message.includes('Failed') ? 'var(--danger)' : 'var(--success)', fontSize: 13,
        }}>
          {message}
        </div>
      )}

      {loading ? (
        <p style={{ color: 'var(--text-secondary)' }}>加载中...</p>
      ) : (
        <>
          <div style={{ marginBottom: 32 }}>
            <h2 style={SECTION_TITLE}>我的技能 ({ownSkills.length})</h2>
            {ownSkills.length === 0 ? (
              <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
                还没有创建技能，点击"新建技能"开始。
              </p>
            ) : (
              <div style={GRID}>
                {ownSkills.map((s) => (
                  <SkillCard
                    key={s.id}
                    skill={s}
                    variant="repo"
                    isActive={activeIds.includes(s.id)}
                    onToggleActive={handleToggleActive}
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                    onPublish={handlePublish}
                  />
                ))}
              </div>
            )}
          </div>

          <div>
            <h2 style={SECTION_TITLE}>已复制的官方技能 ({copiedSkills.length})</h2>
            {copiedSkills.length === 0 ? (
              <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
                前往技能广场浏览和克隆官方技能。
              </p>
            ) : (
              <div style={GRID}>
                {copiedSkills.map((s) => (
                  <SkillCard
                    key={s.id}
                    skill={s}
                    variant="repo"
                    isActive={activeIds.includes(s.id)}
                    onToggleActive={handleToggleActive}
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                  />
                ))}
              </div>
            )}
          </div>
        </>
      )}

      <SkillEditorModal
        open={editorOpen}
        onClose={() => { setEditorOpen(false); setEditSkill(null); }}
        editSkill={editSkill}
        onSaved={handleSaved}
      />
    </div>
  );
}
