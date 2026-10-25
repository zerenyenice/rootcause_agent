# Plan: T008 Service clients layer

Backlog T008 (epic EP0). Branch `feat/service-clients` off `dev`. Gate 1 approved by the project owner on 2026-10-25 ("create clients for our services"). PROJECT.md section 6 "Service clients".

## Goal
One place, `rootcause_core.clients`, builds every external-service client from `Settings`. Agents and services ask for a client by purpose; they never import a provider SDK client class.

## Design
- **Settings (rootcause_core.settings):** nested groups via `env_nested_delimiter="__"` are avoided to keep flat `ROOTCAUSE_*` names. New fields:
  - `llm_provider: Literal["anthropic", "azure_openai"] = "anthropic"`
  - `anthropic_api_key: SecretStr | None`
  - `azure_openai_endpoint: AnyHttpUrl | None`, `azure_openai_api_key: SecretStr | None`, `azure_openai_api_version: str = "2025-04-01-preview"`
  - per-role model/deployment: existing `orchestrator_model`, `fast_model`, `reasoning_model` hold model ids for Anthropic (e.g. `claude-opus-5`, without the `anthropic:` prefix after migration; accept both) and deployment names for Azure OpenAI.
  - `llm_timeout_s = 60`, `llm_max_retries = 2`, `llm_max_tokens = 8192`
  - `embedding_provider: Literal["azure_openai", "openai_compatible"] = "azure_openai"`, `embedding_model` (deployment or model id), `embedding_dimensions: int = 1536`, `embedding_api_base: AnyHttpUrl | None`, `embedding_api_key: SecretStr | None`, `embedding_batch_size = 64`
  - `openviking_api_key: SecretStr | None`
  - Model validator: required fields per selected provider; `ConfigurationError` names missing variables, never values.
- **`rootcause_core/clients/`** (new subpackage):
  - `llm.py`: `chat_model(role: Literal["orchestrator","fast","reasoning"], settings=None) -> BaseChatModel` building `ChatAnthropic` or `AzureChatOpenAI` with timeout, retries, max tokens; cached per (provider, role) with `functools.lru_cache` keyed on a frozen config tuple.
  - `embeddings.py`: `embeddings(settings=None) -> Embeddings` (`AzureOpenAIEmbeddings` or `OpenAIEmbeddings(base_url=...)`), dimensions and chunk size set.
  - `openviking.py`: `openviking_http(settings=None) -> httpx.AsyncClient` with base URL, `Authorization: Bearer` / API key header per OpenViking docs, timeouts; `close_clients()` async closes cached HTTP clients.
  - `errors.py` or reuse `rootcause_core.errors`: client construction errors are `ConfigurationError`.
  - `__init__.py` exports the factories and `reset_clients()` (clears caches; for tests).
- **Orchestrator:** `rootcause_agents.orchestrator.build_orchestrator` uses `chat_model("orchestrator")` when no model is injected (replace `init_chat_model`).
- **Architecture guard:** a test scans source files outside `rootcause_core/clients/` and fails if they import `ChatAnthropic`, `AzureChatOpenAI`, `ChatOpenAI`, `AzureOpenAIEmbeddings`, `OpenAIEmbeddings`, `anthropic.Anthropic`, `openai.OpenAI`/`AzureOpenAI`, or `langfuse.Langfuse` directly.
- Dependencies (packages/core): `langchain-anthropic`, `langchain-openai`, `httpx`; move them out of packages/agents if duplicated.
- Langfuse is T009 and reuses this layer.

## Tests (tester-owned, RED first; no network)
Settings defaults and per-provider required fields (missing Azure endpoint/key when provider is azure_openai -> ConfigurationError naming the variables); `chat_model` returns ChatAnthropic / AzureChatOpenAI with the right model/deployment, timeout, max_retries, max_tokens per role; caching returns the same object until `reset_clients()`; `embeddings()` returns the right class with dimensions and chunk size, both providers; `openviking_http()` base URL and auth header, and `close_clients()` closes it; secrets never appear in `repr` of clients or in error messages; architecture guard test; orchestrator builds its model via `chat_model` (monkeypatch the factory and assert it was called with "orchestrator").

## Acceptance (backlog)
Provider selection and required-setting validation covered without network; no direct provider SDK imports outside the clients layer.
