import { useState } from 'react';

export interface ToolEntryData {
  toolUseId: string;
  toolName: string;
  status: 'unknown' | 'started' | 'completed' | 'failed';
  output: string;
  isError: boolean;
}

interface ToolSidebarProps {
  entries: ToolEntryData[];
  visible: boolean;
  onToggle: () => void;
}

export default function ToolSidebar({ entries, visible, onToggle }: ToolSidebarProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggleEntry = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  if (!visible) {
    return null;
  }

  const statusColor = (status: string) => {
    switch (status) {
      case 'started': return 'var(--link)';
      case 'completed': return 'var(--success)';
      case 'failed': return 'var(--danger)';
      default: return 'var(--text-secondary)';
    }
  };

  return (
    <div style={{
      position: 'fixed', right: 0, top: 0, bottom: 0, width: 300,
      background: '#141430', borderLeft: '1px solid var(--border)',
      zIndex: 99, display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '12px 16px', borderBottom: '1px solid var(--border)',
      }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Tool History</span>
        <button onClick={onToggle} style={{
          background: 'none', border: 'none', color: 'var(--text-secondary)',
          cursor: 'pointer', fontSize: 18,
        }}>
          ✕
        </button>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
        {entries.length === 0 ? (
          <p style={{ fontSize: 13, color: 'var(--text-muted)', textAlign: 'center', marginTop: 24 }}>No tool calls yet.</p>
        ) : (
          entries.map((entry) => (
            <div key={entry.toolUseId} style={{
              background: 'var(--bg-secondary)', borderRadius: 6, marginBottom: 6,
              border: '1px solid var(--border)',
            }}>
              <div
                onClick={() => toggleEntry(entry.toolUseId)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px',
                  cursor: 'pointer', fontSize: 13,
                }}
              >
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: statusColor(entry.status), flexShrink: 0,
                }} />
                <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{entry.toolName}</span>
                <span style={{ color: 'var(--text-secondary)', marginLeft: 'auto', fontSize: 11 }}>
                  {entry.status}
                </span>
              </div>
              {expanded[entry.toolUseId] && entry.output && (
                <pre style={{
                  padding: '8px 10px', fontSize: 12, color: '#aaa',
                  whiteSpace: 'pre-wrap', borderTop: '1px solid var(--border)',
                  margin: 0, maxHeight: 200, overflow: 'auto', background: '#0d1117',
                }}>
                  {entry.output}
                </pre>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
