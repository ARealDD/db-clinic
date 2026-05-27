# -*- coding: utf-8 -*-
"""Verbatim mirror of the Rust `TOOL_USAGE_GUIDANCE` constant in
`rust/crates/agent-grpc-server/src/session_store.rs`.

This block is appended to every session's `system_prompts` kernel-side. The
gateway needs its own copy so the `/api/system_prompt/preview` endpoint can
render the *exact* assembled prompt operators will see in the LLM call.

DRIFT GUARD: `scripts/check-prompt-mirror.sh` diffs this string against the
Rust source. If they diverge, that script fails — kept in sync via fmt --check.
"""

TOOL_USAGE_GUIDANCE = """# Tool usage

- For reading file contents, prefer `read_file` over `bash cat ...` — `read_file` runs locally and returns content immediately; `bash` is dispatched to a human operator and may be rejected.
- For globbing or grepping the workspace, prefer `glob_search` / `grep_search` over `bash find` / `bash grep`.
- `bash` is for shell actions that have no local-tool equivalent (querying databases, inspecting running processes, calling external services, etc.). Every `bash` call is shown to a human operator as an instruction card before it runs.
- When you call `bash`, you MUST set the `description` input field to a short, plain-language statement of WHAT YOU ARE TRYING TO LEARN or ACCOMPLISH (not just a paraphrase of the command). The operator reads this to decide whether to run the command, suggest a different command, or reject the request entirely. A weak or missing description makes rejection more likely.
- If the operator returns `is_error=true` with a text message instead of command output, treat the message as feedback or guidance and adjust your approach — do not retry the same command."""
