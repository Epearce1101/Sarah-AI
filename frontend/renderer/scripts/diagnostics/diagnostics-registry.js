export const DIAGNOSTICS_VERSION = "sarah-diagnostics-v1";
export const TIME_FILTERS = ["1h", "24h", "7d", "30d", "custom"].map((id) => ({ id, label: id === "custom" ? "Custom" : id.toUpperCase() }));
export const PANEL_PRESETS = {
  overview: ["ai_tokens", "ai_latency", "voice_wake", "system_health", "alerts"],
  ai_ops: ["ai_tokens", "ai_latency", "model_ops", "chat_activity", "network_errors"],
  voice_debug: ["voice_wake", "avatar_runtime", "network_errors", "latency_histogram"],
  system_health: ["system_health", "network_errors", "latency_histogram", "alerts"],
  streaming_mode: ["ai_latency", "network_errors", "avatar_runtime", "alerts"],
  full_telemetry: ["ai_tokens", "ai_latency", "model_ops", "chat_activity", "voice_wake", "avatar_runtime", "system_health", "network_errors", "latency_histogram", "alerts"],
};

const m = (id, label, panel, format = "number", unit = "", accent = "cyan", defaultValue = 0) => ({ id, label, panel, format, unit, accent, defaultValue });

export const METRIC_DEFINITIONS = [
  m("ai.input_tokens", "Input Tokens", "ai_tokens", "number", "tok"),
  m("ai.output_tokens", "Output Tokens", "ai_tokens", "number", "tok"),
  m("ai.tokens_per_request_avg", "Avg Tokens / Request", "ai_tokens", "number", "tok"),
  m("ai.tokens_day", "Tokens Today", "ai_tokens", "number", "tok"),
  m("ai.tokens_week", "Tokens This Week", "ai_tokens", "number", "tok"),
  m("ai.tokens_month", "Tokens This Month", "ai_tokens", "number", "tok"),
  m("ai.context_utilization_pct", "Context Utilization", "ai_tokens", "percent", "%", "amber"),
  m("ai.token_cost_day", "Token Cost Today", "ai_tokens", "currency", "$"),
  m("ai.token_cost_week", "Token Cost Week", "ai_tokens", "currency", "$"),
  m("ai.token_cost_month", "Token Cost Month", "ai_tokens", "currency", "$"),
  m("extra.token_efficiency_ratio", "Token Efficiency", "ai_tokens", "ratio", "", "green"),
  m("extra.sanitizer_strip_count", "Sanitizer Strips", "ai_tokens", "number", "", "purple"),
  m("extra.context_rebuild_frequency", "Context Rebuilds", "ai_tokens", "number", "/hr"),

  m("ai.time_to_first_token_ms", "Time To First Token", "ai_latency", "number", "ms", "magenta"),
  m("ai.total_response_time_ms", "Total Response Time", "ai_latency", "number", "ms", "magenta"),
  m("ai.p95_response_time_ms", "p95 Response", "ai_latency", "number", "ms", "amber"),
  m("ai.model_fallback_rate", "Model Fallback Rate", "ai_latency", "percent", "%"),
  m("extra.model_cold_starts", "Model Cold Starts", "ai_latency", "number", "", "amber"),
  m("extra.model_warm_reuse_rate", "Model Warm Reuse", "ai_latency", "percent", "%", "green"),
  m("extra.streaming_interruptions", "Streaming Interruptions", "ai_latency", "number", "", "red"),

  m("model.top_model", "Top Model", "model_ops", "text", "", "magenta", "unknown"),
  m("model.usage_distribution", "Model Usage Distribution", "model_ops", "text", "", "cyan", "collecting"),
  m("model.switch_frequency", "Model Switch Frequency", "model_ops", "number", "/hr"),
  m("model.success_failure_by_model", "Success vs Failure", "model_ops", "text", "", "green", "100 / 0"),

  m("chat.messages_day", "Messages Today", "chat_activity"),
  m("chat.messages_week", "Messages Week", "chat_activity"),
  m("chat.messages_month", "Messages Month", "chat_activity"),
  m("chat.incoming_outgoing_ratio", "In / Out Ratio", "chat_activity", "ratio", "", "cyan", 1),
  m("chat.activity_heatmap", "Activity Heatmap", "chat_activity", "text", "", "cyan", "live"),
  m("chat.conversation_count", "Conversation Count", "chat_activity"),
  m("chat.avg_messages_per_conversation", "Avg Messages / Conversation", "chat_activity"),
  m("chat.sentiment_distribution", "Sentiment Distribution", "chat_activity", "text", "", "cyan", "neutral"),
  m("chat.sentiment_trend", "Sentiment Trend", "chat_activity", "text", "", "cyan", "stable"),
  m("extra.conversation_length_trend", "Conversation Length Trend", "chat_activity", "text", "", "cyan", "stable"),
  m("extra.affinity_trend", "Affinity Trend", "chat_activity", "text", "", "green", "stable"),

  m("voice.wake_trigger_count", "Wake Triggers", "voice_wake", "number", "", "purple"),
  m("voice.wake_reason_breakdown", "Wake Reason Breakdown", "voice_wake", "text", "", "purple", "none"),
  m("voice.false_wake_rate", "False Wake Rate", "voice_wake", "percent", "%", "amber"),
  m("voice.wake_confidence_avg", "Wake Confidence Avg", "voice_wake", "percent", "%", "purple"),
  m("voice.stt_latency_ms", "STT Latency", "voice_wake", "number", "ms"),
  m("voice.tts_generation_ms", "TTS Generation", "voice_wake", "number", "ms"),
  m("voice.errors", "Voice Errors", "voice_wake", "number", "", "red"),
  m("voice.lip_sync_active", "Lip Sync Active", "voice_wake", "bool", "", "green", false),

  m("extra.overlay_emotion_spikes", "Emotion Spike Frequency", "avatar_runtime", "number", "/hr", "magenta"),
  m("extra.avatar_fps_jitter", "Avatar FPS Jitter", "avatar_runtime", "number", "var", "blue"),

  m("system.cpu_pct", "CPU Usage", "system_health", "percent", "%", "blue"),
  m("system.gpu_pct", "GPU Usage", "system_health", "percent", "%", "blue"),
  m("system.ram_pct", "RAM Usage", "system_health", "percent", "%"),
  m("system.vram_pct", "VRAM Usage", "system_health", "percent", "%"),
  m("system.disk_pct", "Disk Usage", "system_health", "percent", "%"),
  m("system.temperature_c", "System Temperature", "system_health", "number", "C", "amber"),
  m("system.fan_rpm", "Fan Speed", "system_health", "number", "rpm"),
  m("system.power_state", "Power / Battery", "system_health", "text", "", "blue", "unknown"),
  m("system.renderer_fps", "Renderer FPS", "system_health", "number", "fps", "green"),
  m("system.dropped_frames", "Dropped Frames", "system_health", "number", "", "amber"),
  m("extra.backend_memory_trend", "Backend Memory Trend", "system_health", "text", "", "blue", "stable"),

  m("network.websocket_latency_ms", "WebSocket Latency", "network_errors", "number", "ms", "cyan"),
  m("network.reconnect_count", "Reconnect Count", "network_errors"),
  m("network.backend_response_ms", "Backend Response", "network_errors", "number", "ms"),
  m("network.request_failure_rate", "Request Failure Rate", "network_errors", "percent", "%", "amber"),
  m("network.packet_loss_pct", "Packet Loss", "network_errors", "percent", "%"),
  m("errors.api_error_rate", "API Error Rate", "network_errors", "percent", "%", "red"),
  m("errors.tool_call_failures", "Tool Call Failures", "network_errors", "number", "", "red"),
  m("errors.retry_count", "Retry Count", "network_errors"),
  m("errors.exception_categories", "Exception Categories", "network_errors", "text", "", "red", "none"),
  m("extra.error_spike_5m", "5-min Error Spike", "network_errors", "bool", "", "red", false),
  m("extra.hallucination_flags", "Hallucination Flags", "network_errors", "number", "", "amber"),
  m("extra.cache_hit_rate", "Cache Hit Rate", "network_errors", "percent", "%", "green"),

  m("alerts.critical", "Critical Alerts", "alerts", "number", "", "red"),
  m("alerts.warning", "Warning Alerts", "alerts", "number", "", "amber"),
  m("extra.latency_histogram", "Latency Histogram", "latency_histogram", "text", "", "cyan", "ready"),
];

export const REQUIRED_METRIC_IDS = METRIC_DEFINITIONS.map((metric) => metric.id);
export const PANEL_DEFINITIONS = [
  { id: "ai_tokens", title: "AI / Token Metrics", accent: "magenta", visual: "gauge" },
  { id: "ai_latency", title: "AI Latency", accent: "amber", visual: "sparkline" },
  { id: "model_ops", title: "Model Ops", accent: "magenta", visual: "donut" },
  { id: "chat_activity", title: "Chat Activity", accent: "cyan", visual: "heatmap" },
  { id: "voice_wake", title: "Voice / Wake", accent: "purple", visual: "bar" },
  { id: "avatar_runtime", title: "Avatar Runtime", accent: "purple", visual: "sparkline" },
  { id: "system_health", title: "System Health", accent: "blue", visual: "gauges" },
  { id: "network_errors", title: "Network / Errors", accent: "red", visual: "stacked" },
  { id: "latency_histogram", title: "Latency Histogram", accent: "cyan", visual: "histogram", metrics: ["extra.latency_histogram", "network.backend_response_ms", "ai.total_response_time_ms", "ai.p95_response_time_ms"] },
  { id: "alerts", title: "Alerts", accent: "red", visual: "alerts", metrics: ["alerts.critical", "alerts.warning", "extra.error_spike_5m", "network.request_failure_rate"] },
].map((panel) => ({ ...panel, metrics: panel.metrics || METRIC_DEFINITIONS.filter((metric) => metric.panel === panel.id).map((metric) => metric.id) }));

export const METRIC_BY_ID = Object.fromEntries(METRIC_DEFINITIONS.map((metric) => [metric.id, metric]));
export const PANEL_BY_ID = Object.fromEntries(PANEL_DEFINITIONS.map((panel) => [panel.id, panel]));
export function getPresetOrder(preset = "full_telemetry") { return [...(PANEL_PRESETS[preset] || PANEL_PRESETS.full_telemetry)]; }
