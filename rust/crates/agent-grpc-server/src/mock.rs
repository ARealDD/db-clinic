use runtime::{ApiClient, ApiRequest, AssistantEvent, RuntimeError, TokenUsage};

pub struct MockApiClient {
    call_count: usize,
}

impl MockApiClient {
    pub fn new() -> Self {
        Self { call_count: 0 }
    }
}

impl ApiClient for MockApiClient {
    fn stream(&mut self, _request: ApiRequest) -> Result<Vec<AssistantEvent>, RuntimeError> {
        self.call_count += 1;
        Ok(vec![
            AssistantEvent::TextDelta(format!(
                "[Mock LLM response #{count}] I received your message. \
                 In production this would be a real LLM response via the configured provider.",
                count = self.call_count
            )),
            AssistantEvent::Usage(TokenUsage {
                input_tokens: 50,
                output_tokens: 30,
                cache_creation_input_tokens: 0,
                cache_read_input_tokens: 0,
            }),
            AssistantEvent::MessageStop,
        ])
    }

    fn model(&self) -> &str {
        "mock"
    }

    fn provider(&self) -> &str {
        "mock"
    }
}
