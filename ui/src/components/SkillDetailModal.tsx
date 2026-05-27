import { useMemo } from 'react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import type { Skill } from '../types';

interface SkillDetailModalProps {
  skill: Skill | null;
  open: boolean;
  onClose: () => void;
  loggedIn: boolean;
  onClone: (skill: Skill) => void;
}

function parseMeta(skill: Skill) {
  try {
    return JSON.parse(skill.metadata || '{}') as Record<string, unknown>;
  } catch {
    return {};
  }
}

const badgeStyle = (color: string): React.CSSProperties => ({
  display: 'inline-block', fontSize: 11, padding: '2px 8px', borderRadius: 10,
  background: color, color: '#fff', fontWeight: 600,
});

export default function SkillDetailModal({ skill, open, onClose, loggedIn, onClone }: SkillDetailModalProps) {
  if (!open || !skill) return null;

  const meta = parseMeta(skill);
  const category = (meta.category as string) || '';
  const skillType = (meta.skill_type as string) || '';
  const keywords = (meta.keywords as string[]) || [];
  const tags = (meta.tags as string[]) || [];
  const isOfficial = skill.is_official === 1;
  const typeLabel = skillType === 'knowledge' ? '知识' : '案例';

  const renderedContent = useMemo(() => {
    if (!skill.content) return '';
    const html = marked.parse(skill.content, { breaks: true, gfm: true }) as string;
    return DOMPurify.sanitize(html);
  }, [skill.content]);

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div style={{
        background: '#1a1d23', borderRadius: 12, width: '100%', maxWidth: 720,
        maxHeight: '90vh', overflow: 'hidden', border: '1px solid #2d3139',
        color: '#e0e0e0', display: 'flex', flexDirection: 'column',
      }} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div style={{
          padding: '20px 24px', borderBottom: '1px solid #2d3139',
          display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
          flexShrink: 0,
        }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
              {isOfficial && <span style={badgeStyle('#0f3460')}>官方</span>}
              {skillType && <span style={badgeStyle('#8b949e')}>{typeLabel}</span>}
              {skill.is_published === 1 && <span style={badgeStyle('#238636')}>已发布</span>}
            </div>
            <h2 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>{skill.name}</h2>
            {category && <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>分类: {category}</p>}
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', color: 'var(--text-secondary)',
            cursor: 'pointer', fontSize: 22, padding: '0 4px', lineHeight: 1,
          }}>✕</button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: '20px 24px' }}>
          {skill.description && (
            <p style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 16, lineHeight: 1.5 }}>
              {skill.description}
            </p>
          )}

          {keywords.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)', marginRight: 6 }}>关键词:</span>
              {keywords.map((kw) => (
                <span key={kw} style={{
                  display: 'inline-block', fontSize: 12, padding: '2px 8px',
                  borderRadius: 10, background: '#0d1117', color: 'var(--link)',
                  marginRight: 4, marginBottom: 4,
                }}>{kw}</span>
              ))}
            </div>
          )}

          {tags.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)', marginRight: 6 }}>标签:</span>
              {tags.map((t) => (
                <span key={t} style={{
                  display: 'inline-block', fontSize: 12, padding: '2px 8px',
                  borderRadius: 10, background: '#0d1117', color: 'var(--text-secondary)',
                  marginRight: 4, marginBottom: 4,
                }}>{t}</span>
              ))}
            </div>
          )}

          {skill.username && (
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 16 }}>作者: {skill.username}</p>
          )}

          {skill.content && (
            <div style={{
              borderTop: '1px solid #2d3139', paddingTop: 16,
            }}>
              <div className="markdown-body" dangerouslySetInnerHTML={{ __html: renderedContent }} />
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '12px 24px', borderTop: '1px solid #2d3139',
          display: 'flex', gap: 8, justifyContent: 'flex-end', flexShrink: 0,
        }}>
          <button onClick={onClose} style={{
            padding: '8px 20px', borderRadius: 6, border: '1px solid #2d3139',
            background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 13,
          }}>
            关闭
          </button>
          <button
            onClick={() => { onClone(skill); onClose(); }}
            disabled={!loggedIn}
            style={{
              padding: '8px 20px', borderRadius: 6, border: 'none', cursor: loggedIn ? 'pointer' : 'default',
              fontSize: 13, fontWeight: 600, background: loggedIn ? 'var(--success)' : '#555', color: '#fff',
              opacity: loggedIn ? 1 : 0.5,
            }}>
            添加到我的仓库
          </button>
        </div>

        <style>{`
          .markdown-body p { margin: 0 0 8px; line-height: 1.6; }
          .markdown-body p:last-child { margin-bottom: 0; }
          .markdown-body pre { background: #0d1117; padding: 12px; border-radius: 6px; overflow-x: auto; margin: 8px 0; }
          .markdown-body code { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 13px; }
          .markdown-body pre code { background: none; padding: 0; }
          .markdown-body :not(pre) > code { background: #2d3139; padding: 2px 6px; border-radius: 4px; }
          .markdown-body table { border-collapse: collapse; margin: 8px 0; width: 100%; font-size: 13px; }
          .markdown-body th, .markdown-body td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
          .markdown-body th { background: #0d1117; font-weight: 600; }
          .markdown-body ul, .markdown-body ol { padding-left: 20px; margin: 4px 0; }
          .markdown-body li { margin: 2px 0; }
          .markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4 { margin: 16px 0 8px; color: var(--link); }
          .markdown-body h1 { font-size: 18px; }
          .markdown-body h2 { font-size: 16px; }
          .markdown-body h3 { font-size: 14px; }
          .markdown-body blockquote { border-left: 3px solid var(--border); padding-left: 12px; color: var(--text-secondary); margin: 8px 0; }
          .markdown-body a { color: var(--link); }
        `}</style>
      </div>
    </div>
  );
}
