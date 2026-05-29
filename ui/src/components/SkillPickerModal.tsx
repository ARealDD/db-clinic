import { useState, useEffect } from 'react';

interface MatchedSkill {
  id: string;
  name: string;
  category: string;
  score: number;
  type: string;
}

interface Props {
  open: boolean;
  skills: MatchedSkill[];
  onConfirm: (ids: string[]) => void;
  onCancel: () => void;
}

const MAX_SELECTION = 3;

const backdropStyle: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  zIndex: 1000,
};

const modalStyle: React.CSSProperties = {
  background: 'var(--bg-primary, #1a1a2e)',
  borderRadius: 12, padding: 24, minWidth: 480, maxWidth: 600,
  maxHeight: '80vh', overflow: 'auto',
  boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
};

export default function SkillPickerModal({ open, skills, onConfirm, onCancel }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (open) {
      // Default: select top 3
      setSelected(new Set(skills.slice(0, MAX_SELECTION).map((s) => s.id)));
    }
  }, [open, skills]);

  if (!open) return null;

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        if (next.size >= MAX_SELECTION) return prev; // Reached max
        next.add(id);
      }
      return next;
    });
  };

  const scoreColor = (score: number): string => {
    if (score >= 3) return 'var(--success, #4caf50)';
    if (score >= 1.5) return 'var(--warning, #ff9800)';
    return 'var(--text-secondary, #888)';
  };

  return (
    <div style={backdropStyle} onClick={onCancel}>
      <div style={modalStyle} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 4px', fontSize: 18, color: 'var(--text-primary)' }}>
          选择诊断技能
        </h3>
        <p style={{ margin: '0 0 16px', fontSize: 13, color: 'var(--text-secondary)' }}>
          选择最多 {MAX_SELECTION} 个技能作为助手的上下文。匹配度最高的 {MAX_SELECTION} 个默认选中。
        </p>

        {skills.length === 0 ? (
          <p style={{ color: 'var(--text-secondary)', padding: 16, textAlign: 'center' }}>
            没有匹配到技能。
          </p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {skills.map((s) => (
              <li
                key={s.id}
                onClick={() => toggle(s.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '10px 12px', marginBottom: 6,
                  borderRadius: 8, cursor: 'pointer',
                  background: selected.has(s.id) ? 'var(--accent-alpha, rgba(99,102,241,0.15))' : 'var(--bg-secondary, #16213e)',
                  border: selected.has(s.id) ? '1px solid var(--accent, #6366f1)' : '1px solid transparent',
                  transition: 'all 0.15s',
                }}
              >
                <input
                  type="checkbox"
                  checked={selected.has(s.id)}
                  onChange={() => toggle(s.id)}
                  style={{ accentColor: 'var(--accent, #6366f1)' }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)' }}>
                    {s.name}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                    {s.category} &middot; {s.type}
                  </div>
                </div>
                <span style={{
                  fontSize: 12, fontWeight: 700, padding: '2px 8px',
                  borderRadius: 4, background: scoreColor(s.score),
                  color: '#fff',
                }}>
                  {s.score.toFixed(1)}
                </span>
              </li>
            ))}
          </ul>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
          <button
            onClick={onCancel}
            style={{
              padding: '8px 16px', borderRadius: 8, border: '1px solid var(--border, #333)',
              background: 'transparent', color: 'var(--text-primary)', cursor: 'pointer', fontSize: 14,
            }}
          >
            取消
          </button>
          <button
            onClick={() => onConfirm(Array.from(selected))}
            disabled={selected.size === 0}
            style={{
              padding: '8px 16px', borderRadius: 8, border: 'none',
              background: selected.size > 0 ? 'var(--accent, #6366f1)' : '#555',
              color: '#fff', cursor: selected.size > 0 ? 'pointer' : 'default', fontSize: 14,
              fontWeight: 600,
            }}
          >
            确认 ({selected.size}/{MAX_SELECTION})
          </button>
        </div>
      </div>
    </div>
  );
}
