import type { Session } from '../types';

interface SessionSidebarProps {
  sessions: Session[];
  activeSessionId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onCreate: () => void;
  loading?: boolean;
}

export default function SessionSidebar({ sessions, activeSessionId, onSelect, onDelete, onCreate, loading }: SessionSidebarProps) {
  const formatSessionName = (s: Session) => {
    const tsMs = s.created_at_ms;
    if (tsMs) {
      const d = new Date(tsMs);
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    const id = s.session_id || '';
    return id.length > 8 ? id.substring(8) : id;
  };

  return (
    <div style={{
      width: 220, flexShrink: 0, borderRight: '1px solid var(--border)',
      background: '#141430', display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <div style={{
        padding: '10px 14px', fontSize: 12, fontWeight: 600, letterSpacing: '0.5px',
        color: '#8888bb', textTransform: 'uppercase', background: '#0f1a35',
        borderBottom: '1px solid var(--border)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <span>Sessions</span>
        <button onClick={onCreate} disabled={loading} style={{
          background: '#0f3460', color: '#4ecca3', border: '1px solid #1a5296',
          borderRadius: 6, padding: '2px 10px', fontSize: 11, cursor: loading ? 'default' : 'pointer',
          fontWeight: 600,
        }}>
          + New
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {sessions.length === 0 && !loading && (
          <div style={{ fontSize: 12, color: '#666', padding: '8px', textAlign: 'center' }}>
            No sessions yet
          </div>
        )}
        {sessions.map((s) => (
          <div
            key={s.session_id}
            onClick={() => onSelect(s.session_id)}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '6px 8px',
              borderRadius: 6, cursor: 'pointer', fontSize: 12,
              color: s.session_id === activeSessionId ? '#4ecca3' : '#8888bb',
              background: s.session_id === activeSessionId ? '#0f3460' : 'transparent',
              transition: 'background 0.1s',
            }}
          >
            <span style={{ flex: 1, fontFamily: '"Cascadia Code", Consolas, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {formatSessionName(s)}
            </span>
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(s.session_id); }}
              style={{
                background: 'none', border: 'none', color: '#666', cursor: 'pointer',
                fontSize: 10, padding: '2px 4px', borderRadius: 4,
                visibility: s.session_id === activeSessionId ? 'visible' : 'hidden',
              }}
              title="Delete session"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
