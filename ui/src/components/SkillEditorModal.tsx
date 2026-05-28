import { useState, useEffect, useRef } from 'react';
import { api } from '../api';
import { useUser } from '../context/UserContext';
import type { Skill } from '../types';

interface SkillEditorModalProps {
  open: boolean;
  onClose: () => void;
  editSkill?: Skill | null;
  onSaved: () => void;
}

const FIELD_LABEL: React.CSSProperties = {
  display: 'block', fontSize: 13, color: '#aaa', marginBottom: 4,
};
const FIELD_INPUT: React.CSSProperties = {
  width: '100%', padding: '8px 10px', border: '1px solid var(--border)',
  borderRadius: 6, background: 'var(--bg-primary)', color: 'var(--text-primary)',
  fontSize: 13, outline: 'none', boxSizing: 'border-box',
};
const FIELD_TEXTAREA: React.CSSProperties = {
  ...FIELD_INPUT, resize: 'vertical', minHeight: 80, fontFamily: 'monospace',
};
const TAB: React.CSSProperties = {
  padding: '8px 20px', cursor: 'pointer', fontSize: 14, fontWeight: 600,
  borderRadius: '8px 8px 0 0', color: 'var(--text-secondary)',
  borderBottom: '2px solid transparent',
};

export default function SkillEditorModal({ open, onClose, editSkill, onSaved }: SkillEditorModalProps) {
  const { user } = useUser();
  const [tab, setTab] = useState<'form' | 'upload'>('form');

  // Form fields
  const [name, setName] = useState('');
  const [skillType, setSkillType] = useState('case');
  const [category, setCategory] = useState('');
  const [description, setDescription] = useState('');
  const [keywords, setKeywords] = useState('');
  const [triggers, setTriggers] = useState('');
  const [symptoms, setSymptoms] = useState('');
  const [tags, setTags] = useState('');
  const [content, setContent] = useState('');

  // Upload fields
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [filePreview, setFilePreview] = useState('');
  const [fileError, setFileError] = useState('');

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setTab('form');
    setError('');
    setSaving(false);
    setUploadFile(null);
    setFilePreview('');
    setFileError('');

    if (editSkill) {
      setName(editSkill.name);
      setDescription(editSkill.description);
      setContent(editSkill.content);
      try {
        const meta = JSON.parse(editSkill.metadata || '{}');
        setSkillType((meta.skill_type as string) || 'case');
        setCategory((meta.category as string) || '');
        setKeywords((meta.keywords as string[] || []).join(', '));
        setTriggers((meta.triggers as string[] || []).join(', '));
        setSymptoms((meta.symptoms as string[] || []).join(', '));
        setTags((meta.tags as string[] || []).join(', '));
      } catch {
        setSkillType('case');
        setCategory('');
        setKeywords('');
        setTriggers('');
        setSymptoms('');
        setTags('');
      }
    } else {
      setName('');
      setSkillType('case');
      setCategory('');
      setDescription('');
      setKeywords('');
      setTriggers('');
      setSymptoms('');
      setTags('');
      setContent('');
    }
  }, [open, editSkill]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadFile(file);
    setFileError('');

    const reader = new FileReader();
    reader.onload = () => {
      const text = reader.result as string;
      setFilePreview(text);
    };
    reader.readAsText(file);
  };

  const buildFormData = () => ({
    name,
    skill_type: skillType || undefined,
    category: category || undefined,
    description,
    keywords: keywords ? keywords.split(',').map((s: string) => s.trim()).filter(Boolean) : undefined,
    triggers: triggers ? triggers.split(',').map((s: string) => s.trim()).filter(Boolean) : undefined,
    symptoms: symptoms ? symptoms.split(',').map((s: string) => s.trim()).filter(Boolean) : undefined,
    tags: tags ? tags.split(',').map((s: string) => s.trim()).filter(Boolean) : undefined,
    content,
  });

  const handleSave = async () => {
    if (!user) return;
    setSaving(true);
    setError('');

    try {
      if (editSkill) {
        await api('/api/skills', {
          method: 'PUT',
          body: { skill_id: editSkill.id, ...buildFormData() },
        });
      } else if (tab === 'upload' && uploadFile) {
        const formData = new FormData();
        formData.append('file', uploadFile);
        await api('/api/skills/upload', { method: 'POST', body: formData });
      } else {
        await api('/api/skills', {
          method: 'POST',
          body: { ...buildFormData() },
        });
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
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
        background: '#1a1d23', borderRadius: 12, width: '100%', maxWidth: 640,
        maxHeight: '90vh', overflow: 'auto', border: '1px solid #2d3139',
        color: '#e0e0e0',
      }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: '20px 24px 0', borderBottom: '1px solid #2d3139', display: 'flex' }}>
          <div onClick={() => setTab('form')} style={{ ...TAB, borderBottomColor: tab === 'form' ? 'var(--accent)' : 'transparent', color: tab === 'form' ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
            表单创建
          </div>
          {!editSkill && (
            <div onClick={() => setTab('upload')} style={{ ...TAB, borderBottomColor: tab === 'upload' ? 'var(--accent)' : 'transparent', color: tab === 'upload' ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
              文件上传
            </div>
          )}
        </div>

        <div style={{ padding: 24 }}>
          {tab === 'form' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <label style={FIELD_LABEL}>技能名称 *</label>
                <input value={name} onChange={(e) => setName(e.target.value)} style={FIELD_INPUT} placeholder="技能名称" />
              </div>
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <label style={FIELD_LABEL}>技能类型</label>
                  <select value={skillType} onChange={(e) => setSkillType(e.target.value)} style={FIELD_INPUT}>
                    <option value="case">诊断案例</option>
                    <option value="knowledge">知识文档</option>
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={FIELD_LABEL}>分类</label>
                  <input value={category} onChange={(e) => setCategory(e.target.value)} style={FIELD_INPUT} placeholder="lock, slow_sql, ..." />
                </div>
              </div>
              <div>
                <label style={FIELD_LABEL}>描述</label>
                <input value={description} onChange={(e) => setDescription(e.target.value)} style={FIELD_INPUT} placeholder="技能描述" />
              </div>
              <div>
                <label style={FIELD_LABEL}>关键词 (逗号分隔)</label>
                <input value={keywords} onChange={(e) => setKeywords(e.target.value)} style={FIELD_INPUT} placeholder="lock, deadlock, 死锁" />
              </div>
              <div>
                <label style={FIELD_LABEL}>触发条件 (逗号分隔)</label>
                <input value={triggers} onChange={(e) => setTriggers(e.target.value)} style={FIELD_INPUT} placeholder="deadlock detected" />
              </div>
              <div>
                <label style={FIELD_LABEL}>症状 (逗号分隔)</label>
                <input value={symptoms} onChange={(e) => setSymptoms(e.target.value)} style={FIELD_INPUT} placeholder="query hanging" />
              </div>
              <div>
                <label style={FIELD_LABEL}>标签 (逗号分隔)</label>
                <input value={tags} onChange={(e) => setTags(e.target.value)} style={FIELD_INPUT} placeholder="gaussdb, performance" />
              </div>
              <div>
                <label style={FIELD_LABEL}>内容 (Markdown)</label>
                <textarea value={content} onChange={(e) => setContent(e.target.value)} style={FIELD_TEXTAREA} rows={8} placeholder="## Skill Content&#10;&#10;Markdown content here..." />
              </div>
            </div>
          )}

          {tab === 'upload' && !editSkill && (
            <div>
              <div style={{
                border: '2px dashed var(--border)', borderRadius: 8, padding: 32,
                textAlign: 'center', cursor: 'pointer', marginBottom: 16,
              }} onClick={() => fileRef.current?.click()}>
                <input ref={fileRef} type="file" accept=".md,.yaml,.yml" onChange={handleFileChange} style={{ display: 'none' }} />
                <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
                  {uploadFile ? uploadFile.name : '点击上传 SKILL.md 文件'}
                </p>
                <p style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 4 }}>
                  支持 .md、.yaml、.yml 格式
                </p>
              </div>
              {filePreview && (
                <div>
                  <label style={FIELD_LABEL}>文件预览</label>
                  <pre style={{
                    background: 'var(--bg-primary)', borderRadius: 6, padding: 12,
                    fontSize: 12, maxHeight: 300, overflow: 'auto',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  }}>{filePreview}</pre>
                </div>
              )}
              {fileError && <p style={{ color: 'var(--danger)', fontSize: 13 }}>{fileError}</p>}
            </div>
          )}

          {error && <p style={{ color: 'var(--danger)', fontSize: 13, marginTop: 12 }}>{error}</p>}

          <div style={{ display: 'flex', gap: 8, marginTop: 20, justifyContent: 'flex-end' }}>
            <button onClick={onClose} style={{
              padding: '8px 20px', borderRadius: 6, border: '1px solid #2d3139',
              background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer',
              fontSize: 13,
            }}>取消</button>
            <button onClick={handleSave} disabled={saving}
              style={{
                padding: '8px 20px', borderRadius: 6, border: 'none',
                background: saving ? '#555' : 'var(--success)', color: '#fff',
                cursor: saving ? 'default' : 'pointer', fontSize: 13, fontWeight: 600,
                opacity: (tab === 'form' && !name) ? 0.5 : 1,
              }}>
              {saving ? '保存中...' : editSkill ? '更新' : '创建'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
