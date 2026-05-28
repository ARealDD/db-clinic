export interface MatchedSkillData {
  id: string;
  name: string;
  category: string;
  score: number;
  type: string;
}

interface SkillSidebarProps {
  skills: MatchedSkillData[];
  visible: boolean;
  onClose: () => void;
  onSelectSkills: () => void;
}

export default function SkillSidebar({ skills, visible, onClose, onSelectSkills }: SkillSidebarProps) {
  if (!visible) return null;

  return (
    <div style={{
      width: 260, flexShrink: 0,
      background: '#141430', borderLeft: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '12px 16px', borderBottom: '1px solid var(--border)',
      }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Matched Skills</span>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', color: 'var(--text-secondary)',
          cursor: 'pointer', fontSize: 18,
        }}>
          ✕
        </button>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
        {skills.length === 0 ? (
          <p style={{ fontSize: 13, color: 'var(--text-muted)', textAlign: 'center', marginTop: 24 }}>
            No skills matched yet.
          </p>
        ) : (
          skills.map((skill) => (
            <div key={skill.id} style={{
              background: 'var(--bg-secondary)', borderRadius: 6, marginBottom: 6,
              border: '1px solid var(--border)', padding: '10px 12px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)' }}>
                  {skill.name}
                </span>
                <span style={{
                  fontSize: 11, padding: '1px 6px', borderRadius: 8,
                  background: 'var(--accent)', color: '#fff', marginLeft: 'auto',
                }}>
                  {skill.score.toFixed(1)}
                </span>
              </div>
              {skill.category && (
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {skill.category}{skill.type ? ` / ${skill.type}` : ''}
                </div>
              )}
            </div>
          ))
        )}
      </div>
      <div style={{ padding: '8px 12px', borderTop: '1px solid var(--border)' }}>
        <button onClick={onSelectSkills} style={{
          width: '100%', padding: '8px', borderRadius: 6, border: '1px solid var(--border)',
          background: 'transparent', color: 'var(--link)', cursor: 'pointer', fontSize: 13,
        }}>
          Manage Skills
        </button>
      </div>
    </div>
  );
}
