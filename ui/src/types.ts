// ---------- User ----------
export interface User {
  id: string;
  username: string;
  role?: string;
  created_at?: string;
}

export interface AdminUser {
  id: number;
  username: string;
  role: string;
  created_at: number;
  skill_count: number;
}

export interface AdminUsersResponse {
  users: AdminUser[];
}

// ---------- LLM Config ----------
export interface LLMConfig {
  provider: string;
  model: string;
  base_url: string;
  api_key?: string;
  has_api_key: boolean;
  /** @deprecated Legacy single-field system prompt — use system_prompt_role + system_prompt_background + system_prompt_rules */
  system_prompt?: string;
  system_prompt_role?: string;
  system_prompt_background?: string;
  system_prompt_rules?: string;
}

// ---------- Session ----------
export interface Session {
  session_id: string;
  created_at_ms: number;
}

// ---------- Skill ----------
export interface Skill {
  id: string;
  name: string;
  description: string;
  content: string;
  metadata: string; // JSON string — parse at render time
  owner_id: string | null;
  is_official: number;
  is_published: number;
  source_skill_id: string | null;
  source_is_official?: number | null; // 1=official source, 0=community source, null=user-created
  created_at: string;
  updated_at: string;
  username?: string; // included in square published skills
}

export interface SkillMeta {
  category?: string;
  skill_type?: string;
  keywords?: string[];
  triggers?: string[];
  symptoms?: string[];
  tags?: string[];
  author?: string;
  [key: string]: unknown;
}

export interface SkillSquareData {
  official: Skill[];
  published: Skill[];
}

export interface MySkillsData {
  skills: Skill[];
  active_ids: string[];
}

// ---------- WebSocket Chat ----------

export interface TextDeltaPayload {
  type: 'text_delta';
  content: string;
}

export interface ThinkingDeltaPayload {
  type: 'thinking_delta';
  content: string;
}

export interface SkillMatchPayload {
  type: 'skill_match';
  skills: Array<{
    id: string;
    name: string;
    category: string;
    score: number;
    type: string;
  }>;
}

export interface ToolExecutionPayload {
  type: 'tool_execution';
  tool_use_id: string;
  tool_name: string;
  status: 'unknown' | 'started' | 'completed' | 'failed';
  output: string;
  is_error: boolean;
}

export interface ProxyInstructionPayload {
  type: 'proxy_instruction';
  instruction_id: string;
  tool_use_id: string;
  tool_name: string;
  command: string;
  target_environment: string;
  expected_format: string;
  hint: string;
  timeout_seconds: number;
  read_only: boolean;
  parameters: Record<string, string>;
  purpose: string;
}

export interface ProxyInstructionExpiredPayload {
  type: 'proxy_instruction_expired';
  instruction_id: string;
  tool_use_id: string;
  reason: string;
}

export interface TurnCompletePayload {
  type: 'turn_complete';
  usage: { input_tokens: number; output_tokens: number };
  message_count: number;
}

export interface ErrorPayload {
  type: 'error';
  code: number;
  message: string;
  recoverable: boolean;
}

export interface UsageUpdatePayload {
  type: 'usage_update';
  input_tokens: number;
  output_tokens: number;
}

export type WsServerMessage =
  | TextDeltaPayload
  | ThinkingDeltaPayload
  | SkillMatchPayload
  | ToolExecutionPayload
  | ProxyInstructionPayload
  | ProxyInstructionExpiredPayload
  | TurnCompletePayload
  | ErrorPayload
  | UsageUpdatePayload;

export type WsClientMessage =
  | { type: 'user_message'; content: string }
  | { type: 'cancel' }
  | { type: 'proxy_result'; instruction_id: string; tool_use_id: string; output: string; is_error: boolean };
