// Phase 11 model/context status helper.
// Kept separate from dashboard.js as the first low-risk renderer extraction.
(function () {
  const DEFAULT_ONLINE_BUDGET = 1000000;
  const DEFAULT_LOCAL_BUDGET = 32000;

  function formatTokenCount(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0) return "0";
    if (n >= 1000000) {
      const m = n / 1000000;
      const rounded = m % 1 === 0 ? m.toFixed(0) : m.toFixed(1);
      return `${rounded}M`;
    }
    if (n >= 1000) {
      const rounded = n % 1000 === 0 ? (n / 1000).toFixed(0) : (n / 1000).toFixed(1);
      return `${rounded}k`;
    }
    return String(Math.round(n));
  }

  function normalize(info = {}) {
    const mode = String(info.mode || "online").toLowerCase() === "local" ? "local" : "online";
    const provider = info.provider || (mode === "local" ? "Ollama" : "OpenRouter");
    const modelName = info.model_name || info.model || (
      mode === "local" ? info.local_model : "nvidia/nemotron-3-ultra-550b-a55b:free"
    );
    const tokenBudget = Number(info.context_window_tokens || info.token_budget || (
      mode === "local" ? DEFAULT_LOCAL_BUDGET : DEFAULT_ONLINE_BUDGET
    ));
    const tokensUsed = Number(info.tokens_used || info.context_used_tokens || 0);
    const ratio = tokenBudget > 0 ? tokensUsed / tokenBudget : 0;
    const percent = tokenBudget > 0 ? Math.min(100, Math.max(0, Math.round(ratio * 100))) : 0;
    const fitState = ratio >= 0.9 ? "critical" : ratio >= 0.7 ? "warning" : "normal";
    const autoRouted = Boolean(info.auto_routed || String(modelName).toLowerCase() === "openrouter/auto");
    // Mirrors backend _format_model_label: "vendor/some-model:free" -> "Some Model (free)".
    const [modelBase, modelVariant] = String(modelName || "").split("/", 2).pop().split(":");
    const modelShort = modelBase.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
      + (modelVariant ? ` (${modelVariant})` : "");
    const modelLabel = mode === "local"
      ? (info.model_label || "Local fallback")
      : (info.model_label || (
          autoRouted ? "OpenRouter Auto" : `OpenRouter - ${modelShort || "Auto"}`
        ));
    const contextLabel = `${formatTokenCount(tokensUsed)} / ${formatTokenCount(tokenBudget)}`;
    const windowLabel = `${formatTokenCount(tokenBudget)} window`;

    return {
      mode,
      provider,
      modelName: modelName || "",
      modelLabel,
      tokenBudget,
      tokensUsed,
      percent,
      fitState,
      autoRouted,
      contextLabel,
      percentLabel: `${percent}%`,
      windowLabel,
    };
  }

  function render(elements, info = {}) {
    const status = normalize(info);

    if (elements?.modeButton) {
      const label = status.mode === "online" ? "OPENROUTER" : "LOCAL FALLBACK";
      elements.modeButton.textContent = `LLM: ${label}`;
      elements.modeButton.dataset.llmMode = status.mode;
      elements.modeButton.dataset.provider = status.provider;
    }

    if (elements?.modelText) {
      elements.modelText.textContent = status.modelLabel;
      elements.modelText.dataset.fitState = status.fitState;
    }

    if (elements?.contextText) {
      elements.contextText.textContent = status.windowLabel;
      elements.contextText.dataset.fitState = status.fitState;
    }

    return status;
  }

  window.SARAH_MODEL_STATUS = {
    DEFAULT_ONLINE_BUDGET,
    DEFAULT_LOCAL_BUDGET,
    formatTokenCount,
    normalize,
    render,
  };
})();
