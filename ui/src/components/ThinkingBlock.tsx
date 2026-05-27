import { useState } from 'react';

interface ThinkingBlockProps {
  content: string;
}

export default function ThinkingBlock({ content }: ThinkingBlockProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div style={{
      background: '#2d3139', borderRadius: 8, marginBottom: 12, overflow: 'hidden',
      border: '1px solid var(--border)',
    }}>
      <div
        onClick={() => setCollapsed(!collapsed)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px',
          cursor: 'pointer', fontSize: 13, color: 'var(--text-secondary)',
          userSelect: 'none',
        }}
      >
        <span style={{ transform: collapsed ? 'rotate(-90deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}>▼</span>
        <span>Thinking</span>
      </div>
      {!collapsed && (
        <div style={{
          padding: '0 12px 12px', fontSize: 13, color: '#aaa',
          whiteSpace: 'pre-wrap', lineHeight: 1.5, fontFamily: 'monospace',
        }}>
          {content}
        </div>
      )}
    </div>
  );
}
