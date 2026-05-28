import { useState } from 'react';

interface ProxyCardData {
  instructionId: string;
  toolUseId: string;
  toolName: string;
  command: string;
  purpose: string;
  hint: string;
  targetEnvironment: string;
  timeoutSeconds: number;
  readOnly: boolean;
  status: 'active' | 'submitted' | 'expired' | 'archived';
}

interface ProxyCardProps {
  instruction: ProxyCardData;
  onSubmitResult: (data: { instruction_id: string; tool_use_id: string; output: string; is_error: boolean }) => void;
}

const cardStyle: React.CSSProperties = {
  background: '#1e2128', borderRadius: 8, marginBottom: 12, overflow: 'hidden',
  border: '1px solid var(--border)',
};

export default function ProxyCard({ instruction, onSubmitResult }: ProxyCardProps) {
  const [result, setResult] = useState('');
  const isActive = instruction.status === 'active';

  return (
    <div style={{
      ...cardStyle,
      opacity: isActive ? 1 : 0.5,
      borderColor: isActive ? '#d29922' : 'var(--border)',
      boxShadow: isActive ? '0 0 12px rgba(210, 153, 34, 0.2)' : 'none',
    }}>
      <div style={{
        padding: '10px 14px', borderBottom: '1px solid var(--border)',
        background: isActive ? 'rgba(210, 153, 34, 0.08)' : 'transparent',
      }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
          <span style={{
            fontSize: 11, padding: '2px 8px', borderRadius: 10,
            background: isActive ? '#d29922' : '#555', color: '#fff', fontWeight: 600,
          }}>
            {isActive ? '等待你输入' : '已完成'}
          </span>
          <span style={{ fontSize: 13, color: 'var(--link)', fontWeight: 600 }}>{instruction.toolName}</span>
          {instruction.readOnly && <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>read-only</span>}
        </div>
        {isActive && (
          <div style={{ fontSize: 12, color: '#d29922', marginBottom: 6 }}>
            ⬇️ 请执行以下命令，将结果粘贴到下方文本框后提交
          </div>
        )}
        {instruction.purpose && (
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6 }}>{instruction.purpose}</p>
        )}
        {instruction.command && (
          <pre style={{
            background: '#0d1117', padding: 10, borderRadius: 6, fontSize: 13,
            whiteSpace: 'pre-wrap', margin: 0, color: '#e0e0e0',
          }}>{instruction.command}</pre>
        )}
        {instruction.hint && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6 }}>💡 {instruction.hint}</p>
        )}
      </div>

      {isActive && (
        <div style={{ padding: '10px 14px' }}>
          <textarea
            value={result}
            onChange={(e) => setResult(e.target.value)}
            placeholder="在此粘贴命令执行结果..."
            rows={3}
            style={{
              width: '100%', padding: 10, borderRadius: 6, border: '1px solid var(--border)',
              background: '#0d1117', color: '#e0e0e0', fontSize: 13, fontFamily: 'monospace',
              resize: 'vertical', outline: 'none', boxSizing: 'border-box',
            }}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button
              onClick={() => onSubmitResult({
                instruction_id: instruction.instructionId,
                tool_use_id: instruction.toolUseId,
                output: result,
                is_error: false,
              })}
              disabled={!result.trim()}
              style={{
                padding: '6px 16px', borderRadius: 6, border: 'none',
                cursor: result.trim() ? 'pointer' : 'default',
                fontSize: 13, fontWeight: 600,
                background: result.trim() ? '#238636' : '#555',
                color: '#fff',
              }}
            >
              提交结果
            </button>
            <button
              onClick={() => onSubmitResult({
                instruction_id: instruction.instructionId,
                tool_use_id: instruction.toolUseId,
                output: 'user rejected the instruction without further feedback',
                is_error: true,
              })}
              style={{
                padding: '6px 16px', borderRadius: 6, border: '1px solid var(--danger)',
                background: 'transparent', color: 'var(--danger)', cursor: 'pointer', fontSize: 13,
              }}
            >
              拒绝
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
