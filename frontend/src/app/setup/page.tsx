"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  Key,
  CheckCircle2,
  XCircle,
  Loader2,
  AlertCircle,
  ArrowRight,
} from "lucide-react";
import { useTestApiKey, useSaveApiKeys } from "@/hooks/use-setup";
import type { LLMProvider, SaveKeysPayload } from "@/lib/api";

const PROVIDERS: {
  id: LLMProvider;
  label: string;
  helpUrl: string;
  helpLabel: string;
  keyPlaceholder: string;
  description: string;
}[] = [
  {
    id: "openai",
    label: "OpenAI",
    helpUrl: "https://platform.openai.com/api-keys",
    helpLabel: "platform.openai.com/api-keys",
    keyPlaceholder: "sk-...",
    description: "Default. Most-tested path. Uses GPT-class models.",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    helpUrl: "https://console.anthropic.com/",
    helpLabel: "console.anthropic.com",
    keyPlaceholder: "sk-ant-...",
    description: "Uses Claude models via Anthropic's OpenAI-compatible endpoint.",
  },
  {
    id: "ollama",
    label: "Ollama (local)",
    helpUrl: "https://ollama.com",
    helpLabel: "ollama.com",
    keyPlaceholder: "http://host.docker.internal:11434/v1",
    description: "Runs locally — no API key needed. Set the base URL of your Ollama instance.",
  },
];

export default function SetupPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const testMutation = useTestApiKey();
  const saveMutation = useSaveApiKeys();

  const [provider, setProvider] = useState<LLMProvider>("openai");
  const [openaiKey, setOpenaiKey] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [ollamaBaseUrl, setOllamaBaseUrl] = useState("http://host.docker.internal:11434/v1");
  const [tested, setTested] = useState(false);

  const testResult = testMutation.data;
  const meta = PROVIDERS.find((p) => p.id === provider)!;

  // Provider key value the user is entering right now (used by Test + Save)
  const providerKeyValue =
    provider === "openai" ? openaiKey
    : provider === "anthropic" ? anthropicKey
    : ollamaBaseUrl;

  // Only OpenAI keys are validated against the live API. Anthropic + Ollama
  // are saved without validation; the first generation surfaces a bad key.
  const supportsTest = provider === "openai";
  const canSave = supportsTest
    ? Boolean(testResult?.valid)
    : Boolean(providerKeyValue.trim());

  const handleTest = () => {
    if (!openaiKey.trim()) return;
    setTested(false);
    testMutation.mutate(openaiKey.trim(), {
      onSuccess: () => setTested(true),
      onError: () => setTested(true),
    });
  };

  const handleSave = async () => {
    if (!canSave) return;
    const payload: SaveKeysPayload = { llm_provider: provider };
    if (openaiKey.trim()) payload.openai_api_key = openaiKey.trim();
    if (anthropicKey.trim()) payload.anthropic_api_key = anthropicKey.trim();
    if (ollamaBaseUrl.trim()) payload.ollama_base_url = ollamaBaseUrl.trim();

    try {
      await saveMutation.mutateAsync(payload);
      queryClient.invalidateQueries({ queryKey: ["setup-status"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding-status"] });
      router.push("/onboarding");
    } catch {
      // Error shown via mutation state below
    }
  };

  return (
    <div className="max-w-lg mx-auto py-12 px-4">
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-2">
          <Key className="h-6 w-6" />
          <h1 className="text-xl font-bold">Welcome to Mirror</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          Pick an LLM provider and enter your API key. It powers all AI
          features — resume parsing, job scoring, profile building.
          Your key stays on your machine and is stored in your local
          Postgres database.
        </p>
      </div>

      <div className="space-y-5">
        {/* Provider selector */}
        <div>
          <label className="block text-sm font-medium mb-2">
            LLM Provider
          </label>
          <div className="grid grid-cols-3 gap-2">
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  setProvider(p.id);
                  setTested(false);
                  testMutation.reset();
                }}
                className={`px-3 py-2 text-sm rounded border transition-colors ${
                  provider === p.id
                    ? "border-foreground bg-foreground/5 font-medium"
                    : "border-gray-300 hover:border-gray-400"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            {meta.description}
          </p>
        </div>

        {/* Provider key input — conditional on selected provider */}
        <div>
          <label className="block text-sm font-medium mb-1">
            {provider === "ollama" ? "Ollama base URL" : `${meta.label} API Key`}{" "}
            <span className="text-red-500">*</span>
          </label>
          <div className="flex gap-2">
            <input
              type={provider === "ollama" ? "text" : "password"}
              value={providerKeyValue}
              onChange={(e) => {
                const v = e.target.value;
                if (provider === "openai") setOpenaiKey(v);
                else if (provider === "anthropic") setAnthropicKey(v);
                else setOllamaBaseUrl(v);
                setTested(false);
                testMutation.reset();
              }}
              className="flex-1 rounded border px-3 py-2 text-sm font-mono"
              placeholder={meta.keyPlaceholder}
            />
            {supportsTest && (
              <button
                onClick={handleTest}
                disabled={!providerKeyValue.trim() || testMutation.isPending}
                className="px-3 py-2 bg-gray-100 border rounded text-sm font-medium disabled:opacity-50 hover:bg-gray-200 transition-colors shrink-0"
              >
                {testMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Test"
                )}
              </button>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {provider === "ollama" ? (
              "If running Ollama on your host, use http://host.docker.internal:11434/v1 from inside Docker."
            ) : (
              <>
                Get a key at{" "}
                <a
                  href={meta.helpUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  {meta.helpLabel}
                </a>
              </>
            )}
          </p>
        </div>

        {/* Test result — OpenAI only */}
        {supportsTest && tested && testMutation.isError && (
          <div className="flex items-start gap-2 text-sm rounded-lg p-3 bg-red-50 border border-red-200">
            <XCircle className="h-4 w-4 text-red-500 mt-0.5 shrink-0" />
            <p className="text-red-700">
              Network error: {(testMutation.error as Error).message}
            </p>
          </div>
        )}
        {supportsTest && tested && testResult && (
          <div
            className={`flex items-start gap-2 text-sm rounded-lg p-3 ${
              testResult.valid
                ? "bg-green-50 border border-green-200"
                : "bg-red-50 border border-red-200"
            }`}
          >
            {testResult.valid ? (
              <CheckCircle2 className="h-4 w-4 text-green-600 mt-0.5 shrink-0" />
            ) : (
              <XCircle className="h-4 w-4 text-red-500 mt-0.5 shrink-0" />
            )}
            <div>
              {testResult.valid ? (
                <>
                  <p className="font-medium text-green-800">Key is valid</p>
                  {testResult.rate_limits && (
                    <div className="mt-1 text-xs text-green-700 space-y-0.5">
                      {testResult.rate_limits.requests_per_minute && (
                        <p>
                          Rate limit:{" "}
                          {testResult.rate_limits.requests_per_minute.toLocaleString()}{" "}
                          requests/min
                        </p>
                      )}
                      {testResult.rate_limits.tokens_per_minute && (
                        <p>
                          Token limit:{" "}
                          {testResult.rate_limits.tokens_per_minute.toLocaleString()}{" "}
                          tokens/min
                        </p>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <p className="text-red-700">{testResult.error}</p>
              )}
            </div>
          </div>
        )}

        {!supportsTest && (
          <p className="text-xs text-muted-foreground italic">
            {provider === "anthropic"
              ? "Anthropic keys aren't validated here — first AI generation will surface a bad key."
              : "Ollama runs locally, no validation needed. Make sure your Ollama instance is reachable from the Docker network."}
          </p>
        )}

        {/* Web search backends are no longer user-configurable —
            company research uses the LLM provider's native web search
            (OpenAI/Anthropic) with SearXNG as the always-on fallback. */}

        {/* Save error */}
        {saveMutation.isError && (
          <div className="flex items-start gap-2 text-sm text-red-600 bg-red-50 rounded-lg p-3">
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>
              {(saveMutation.error as Error)?.message || "Failed to save. Please try again."}
            </span>
          </div>
        )}

        {/* Save button */}
        <button
          onClick={handleSave}
          disabled={!canSave || saveMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-foreground text-background rounded-md text-sm font-medium disabled:opacity-50 hover:opacity-90 transition-opacity"
        >
          {saveMutation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Saving...
            </>
          ) : (
            <>
              Save & Continue
              <ArrowRight className="h-4 w-4" />
            </>
          )}
        </button>
      </div>
    </div>
  );
}
