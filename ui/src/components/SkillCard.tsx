import type { Skill } from '../types';

interface SkillCardProps {
  skill: Skill;
  /** For square: shows "add to repo" button. For my-skills: shows action buttons. */
  variant: 'square' | 'repo';
  /** Whether the skill is active (repo variant) */
  isActive?: boolean;
  /** Whether user is logged in (square variant only) */
  loggedIn?: boolean;
  onClick?: (skill: Skill) => void;
  onClone?: (skill: Skill) => void;
  onToggleActive?: (skill: Skill) => void;
  onEdit?: (skill: Skill) => void;
  onDelete?: (skill: Skill) => void;
  onPublish?: (skill: Skill) => void;
}

function parseMeta(skill: Skill) {
  try {
    return JSON.parse(skill.metadata || '{}') as Record<string, unknown>;
  } catch {
    return {};
  }
}

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border)',
  borderRadius: 10, padding: 16, display: 'flex', flexDirection: 'column', gap: 8,
};
const badgeStyle = (color: string): React.CSSProperties => ({
  display: 'inline-block', fontSize: 11, padding: '2px 8px', borderRadius: 10,
  background: color, color: '#fff', fontWeight: 600,
});
const btnGroup: React.CSSProperties = {
  display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 'auto', paddingTop: 8,
};

export default function SkillCard({ skill, variant, isActive, loggedIn, onClick, onClone, onToggleActive, onEdit, onDelete, onPublish }: SkillCardProps) {
  const meta = parseMeta(skill);
  const category = (meta.category as string) || '';
  const skillType = (meta.skill_type as string) || '';
  const isOfficial = skill.is_official === 1;
  const isOwn = skill.owner_id !== null && !skill.source_skill_id;
  const isCopied = !!skill.source_skill_id;

  const typeLabel = skillType === 'knowledge' ? '知识' : '案例';

  const handleClick = () => {
    if (variant === 'square') onClick?.(skill);
  };

  return (
    <div style={{ ...cardStyle, cursor: variant === 'square' ? 'pointer' : 'default' }} onClick={handleClick}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        {isOfficial && <span style={badgeStyle('#0f3460')}>官方</span>}
        {isOwn && <span style={badgeStyle('#238636')}>原创</span>}
        {isCopied && <span style={badgeStyle('#d29922')}>克隆</span>}
        {skillType && <span style={badgeStyle('#8b949e')}>{typeLabel}</span>}
        {isActive !== undefined && (
          <span style={{ fontSize: 12, color: isActive ? 'var(--success)' : 'var(--text-muted)', marginLeft: 4 }}>
            {isActive ? '已激活' : '未激活'}
          </span>
        )}
      </div>
      <div style={{ fontWeight: 600, fontSize: 15 }}>{skill.name}</div>
      {category && <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>分类: {category}</div>}
      {skill.description && (
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.4,
          display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
          {skill.description}
        </div>
      )}
      {skill.username && (
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>作者: {skill.username}</div>
      )}

      {variant === 'square' && onClone && (
        <div style={btnGroup}>
          <button
            onClick={(e) => { e.stopPropagation(); onClone(skill); }}
            disabled={!loggedIn}
            style={{
              padding: '6px 14px', border: 'none', borderRadius: 6, cursor: loggedIn ? 'pointer' : 'default',
              fontSize: 12, fontWeight: 600, background: loggedIn ? 'var(--success)' : '#555', color: '#fff',
              opacity: loggedIn ? 1 : 0.5,
            }}>
            添加到我的仓库
          </button>
        </div>
      )}

      {variant === 'repo' && (
        <div style={btnGroup}>
          <button onClick={() => onToggleActive?.(skill)}
            style={{
              padding: '6px 14px', border: `1px solid var(--border)`, borderRadius: 6, cursor: 'pointer',
              fontSize: 12, background: isActive ? '#1a4a8a' : 'transparent',
              color: isActive ? 'var(--link)' : 'var(--text-secondary)',
            }}>
            {isActive ? '已激活' : '未激活'}
          </button>
          {!isOfficial && (
            <>
              <button onClick={() => onEdit?.(skill)}
                style={{
                  padding: '6px 14px', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer',
                  fontSize: 12, background: 'transparent', color: 'var(--text-primary)',
                }}>
                编辑
              </button>
              {!isCopied && (
                <button onClick={() => onPublish?.(skill)}
                  style={{
                    padding: '6px 14px', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer',
                    fontSize: 12, background: 'transparent',
                    color: skill.is_published ? 'var(--warning)' : 'var(--text-secondary)',
                  }}>
                  {skill.is_published ? '取消发布' : '发布'}
                </button>
              )}
              <button onClick={() => onDelete?.(skill)}
                style={{
                  padding: '6px 14px', border: '1px solid var(--danger)', borderRadius: 6, cursor: 'pointer',
                  fontSize: 12, background: 'transparent', color: 'var(--danger)',
                }}>
                删除
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
