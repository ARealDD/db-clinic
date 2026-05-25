use std::collections::HashMap;

use crate::proto;

pub fn build_instruction_card(tool_name: &str, input_json: &str) -> proto::InstructionCard {
    match tool_name {
        "bash" | "Bash" => build_bash_card(input_json),
        _ => build_default_card(tool_name, input_json),
    }
}

fn build_bash_card(input_json: &str) -> proto::InstructionCard {
    let parsed: serde_json::Value =
        serde_json::from_str(input_json).unwrap_or(serde_json::Value::Null);
    let command = parsed
        .get("command")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .to_string();
    let purpose = parsed
        .get("description")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .to_string();
    let timeout = parsed
        .get("timeout")
        .and_then(serde_json::Value::as_u64)
        .and_then(|t| u32::try_from(t / 1000).ok())
        .unwrap_or(0);

    let mut parameters: HashMap<String, String> = HashMap::new();
    if let Some(obj) = parsed.as_object() {
        for (k, v) in obj {
            if k == "command" || k == "description" || k == "timeout" {
                continue;
            }
            let value_str = match v {
                serde_json::Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            parameters.insert(k.clone(), value_str);
        }
    }

    proto::InstructionCard {
        command,
        target_environment: "user-controlled shell (bash/PowerShell)".to_string(),
        expected_format: "text".to_string(),
        hint: "Run the command and paste the output, or reject with feedback \
               (the agent will read it and adjust)."
            .to_string(),
        timeout_seconds: timeout,
        read_only: false,
        parameters,
        purpose,
    }
}

fn build_default_card(tool_name: &str, input_json: &str) -> proto::InstructionCard {
    let display_command = format!("# Tool: {tool_name}\n# Input JSON:\n{input_json}");
    proto::InstructionCard {
        command: display_command,
        target_environment: "user-controlled environment".to_string(),
        expected_format: "text".to_string(),
        hint: format!(
            "The agent kernel requested tool `{tool_name}`. Execute the equivalent action in your environment and paste the textual result below."
        ),
        timeout_seconds: 0,
        read_only: false,
        parameters: HashMap::new(),
        purpose: String::new(),
    }
}
