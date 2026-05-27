import { useMemo } from 'react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

interface ChatMessageProps {
  role: 'user' | 'assistant' | 'system';
  content: string;
  isStreaming?: boolean;
}

function renderMarkdown(raw: string): string {
  const html = marked.parse(raw, { breaks: true, gfm: true }) as string;
  return DOMPurify.sanitize(html, { ADD_TAGS: ['hljs-keyword', 'hljs-string'] });
}

const msgStyle: React.CSSProperties = {
  padding: '12px 16px', borderRadius: 10, maxWidth: '85%',
  lineHeight: 1.5, fontSize: 14, marginBottom: 12,
};

export default function ChatMessage({ role, content, isStreaming }: ChatMessageProps) {
  const html = useMemo(() => renderMarkdown(content), [content]);

  const style: React.CSSProperties = {
    ...msgStyle,
    alignSelf: role === 'user' ? 'flex-end' : 'flex-start',
    background: role === 'user' ? '#0f3460' : role === 'system' ? '#2d3139' : '#16213e',
    color: '#e0e0e0',
    width: role === 'system' ? '100%' : undefined,
    maxWidth: role === 'system' ? '100%' : '85%',
  };

  if (role === 'user') {
    return (
      <div style={style}>
        <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', margin: 0 }}>{content}</pre>
      </div>
    );
  }

  return (
    <div style={style}>
      <div className="markdown-body" dangerouslySetInnerHTML={{ __html: html }} />
      {isStreaming && <span style={{ display: 'inline-block', width: 8, height: 16, background: 'var(--accent)', animation: 'blink 0.8s infinite', marginLeft: 4 }} />}
      <style>{`@keyframes blink { 0%,50% { opacity:1; } 51%,100% { opacity:0; } }`}</style>
      {/* Basic markdown styling */}
      <style>{`
        .markdown-body p { margin: 0 0 8px; }
        .markdown-body p:last-child { margin-bottom: 0; }
        .markdown-body pre { background: #0d1117; padding: 12px; border-radius: 6px; overflow-x: auto; margin: 8px 0; }
        .markdown-body code { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 13px; }
        .markdown-body pre code { background: none; padding: 0; }
        .markdown-body :not(pre) > code { background: #2d3139; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
        .markdown-body table { border-collapse: collapse; margin: 8px 0; width: 100%; }
        .markdown-body th, .markdown-body td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
        .markdown-body th { background: #0d1117; font-weight: 600; }
        .markdown-body ul, .markdown-body ol { padding-left: 20px; margin: 4px 0; }
        .markdown-body li { margin: 2px 0; }
        .markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4 { margin: 12px 0 6px; color: var(--link); }
        .markdown-body blockquote { border-left: 3px solid var(--border); padding-left: 12px; color: var(--text-secondary); margin: 8px 0; }
        .markdown-body a { color: var(--link); }
      `}</style>
    </div>
  );
}
