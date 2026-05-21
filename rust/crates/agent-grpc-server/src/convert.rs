use crate::proto;

pub fn runtime_role_to_proto(role: runtime::MessageRole) -> i32 {
    match role {
        runtime::MessageRole::System => proto::MessageRole::System.into(),
        runtime::MessageRole::User => proto::MessageRole::User.into(),
        runtime::MessageRole::Assistant => proto::MessageRole::Assistant.into(),
        runtime::MessageRole::Tool => proto::MessageRole::Tool.into(),
    }
}

pub fn runtime_content_block_to_proto(block: &runtime::ContentBlock) -> proto::ContentBlock {
    let block_oneof = match block {
        runtime::ContentBlock::Text { text } => {
            proto::content_block::Block::Text(proto::TextContent {
                text: text.clone(),
            })
        }
        runtime::ContentBlock::Thinking {
            thinking,
            signature,
        } => proto::content_block::Block::Thinking(proto::ThinkingContent {
            thinking: thinking.clone(),
            signature: signature.clone(),
        }),
        runtime::ContentBlock::ToolUse { id, name, input } => {
            proto::content_block::Block::ToolUse(proto::ToolUseContent {
                id: id.clone(),
                name: name.clone(),
                input: input.clone(),
            })
        }
        runtime::ContentBlock::ToolResult {
            tool_use_id,
            tool_name,
            output,
            is_error,
        } => proto::content_block::Block::ToolResult(proto::ToolResultContent {
            tool_use_id: tool_use_id.clone(),
            tool_name: tool_name.clone(),
            output: output.clone(),
            is_error: *is_error,
        }),
    };
    proto::ContentBlock {
        block: Some(block_oneof),
    }
}

pub fn runtime_message_to_proto(msg: &runtime::ConversationMessage) -> proto::ConversationMessage {
    proto::ConversationMessage {
        role: runtime_role_to_proto(msg.role),
        blocks: msg.blocks.iter().map(runtime_content_block_to_proto).collect(),
        usage: msg.usage.as_ref().map(runtime_usage_to_proto),
    }
}

pub fn runtime_usage_to_proto(usage: &runtime::TokenUsage) -> proto::TokenUsage {
    proto::TokenUsage {
        input_tokens: usage.input_tokens,
        output_tokens: usage.output_tokens,
        cache_creation_input_tokens: usage.cache_creation_input_tokens,
        cache_read_input_tokens: usage.cache_read_input_tokens,
    }
}

pub fn turn_summary_to_turn_complete(summary: &runtime::TurnSummary) -> proto::TurnComplete {
    let mut messages = Vec::new();
    for msg in &summary.assistant_messages {
        messages.push(runtime_message_to_proto(msg));
    }
    for msg in &summary.tool_results {
        messages.push(runtime_message_to_proto(msg));
    }
    proto::TurnComplete {
        messages,
        turn_usage: Some(runtime_usage_to_proto(&summary.usage)),
        stop_reason: if summary.iterations > 0 {
            proto::TurnStopReason::TurnStopEndTurn.into()
        } else {
            proto::TurnStopReason::Unspecified.into()
        },
    }
}
