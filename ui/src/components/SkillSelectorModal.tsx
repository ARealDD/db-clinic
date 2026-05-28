import { useState, useEffect } from 'react';
import { api } from '../api';
import { useUser } from '../context/UserContext';
import type { Skill, MySkillsData } from '../types';

interface SkillSelectorModalProps {
  open: boolean;
  onClose: () => void;
}

export default function SkillSelectorModal({ open, onClose }: SkillSelectorModalProps) {
  const { user } = useUser();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [activeIds, setActiveIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open || !user) return;
    setLoading(true);
    api<MySkillsData>('/api/skills/mine')
      .then((data) => {
        setSkills(data.skills);
        setActiveIds(new Set(data.active_ids));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open, user]);

  const toggle = (id: string) => {
    setActiveIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const save = async () => {
    if (!user) return;
    setSaving(true);
    try {
      // Load current state to determine what changed
      const current = await api<MySkillsData>(`/api/skills/mine?user_id=${encodeURIComponent(user.id)}`);
      const currentSet = new Set(current.active_ids);

      for (const skill of skills) {
        const wasActive = currentSet.has(skill.id);
        const nowActive = activeIds.has(skill.id);
        if (wasActive !== nowActive) {
          await api('/api/skills/toggle-active', {
            method: 'POST', body: { skill_id: skill.id },
          });
        }
      }
      onClose();
    } catch (e) {
      console.error('Failed to save skill selection', e);
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div style={{
        background: '#1a1d23', borderRadius: 12, width: '100%', maxWidth: 480,
        maxHeight: '80vh', overflow: 'auto', border: '1px solid #2d3139',
        color: '#e0e0e0',
      }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ padding: '16px 20px', margin: 0, borderBottom: '1px solid #2d3139', fontSize: 16 }}>
          选择诊断技能
        </h3>

        {loading ? (
          <p style={{ padding: 20, color: 'var(--text-secondary)', fontSize: 14 }}>加载中...</p>
        ) : skills.length === 0 ? (
          <p style={{ padding: 20, color: 'var(--text-secondary)', fontSize: 14 }}>
            暂无技能。前往技能广场添加技能。
          </p>
        ) : (
          <div style={{ padding: 8 }}>
            {skills.map((skill) => (
              <label key={skill.id} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px',
                cursor: 'pointer', borderRadius: 6, transition: 'background 0.1s',
              }}>
                <input
                  type="checkbox"
                  checked={activeIds.has(skill.id)}
                  onChange={() => toggle(skill.id)}
                  style={{ width: 16, height: 16, cursor: 'pointer' }}
                />
                <div style={{ flex: 1, fontSize: 14 }}>
                  <div style={{ fontWeight: 500 }}>{skill.name}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {skill.is_official ? '官方' : '个人'} · {skill.source_skill_id ? '克隆' : '原创'}
                  </div>
                </div>
              </label>
            ))}
          </div>
        )}

        <div style={{ padding: '12px 20px', borderTop: '1px solid #2d3139', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{
            padding: '8px 20px', borderRadius: 6, border: '1px solid #2d3139',
            background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 13,
          }}>
            取消
          </button>
          <button onClick={save} disabled={saving} style={{
            padding: '8px 20px', borderRadius: 6, border: 'none',
            background: saving ? '#555' : 'var(--success)', color: '#fff',
            cursor: saving ? 'default' : 'pointer', fontSize: 13, fontWeight: 600,
          }}>
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}
