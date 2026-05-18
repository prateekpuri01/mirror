"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChatMessageRead } from "@/lib/types";
import { clearChat, fetchChatMessages, getApiUrl } from "@/lib/api";

interface UseChatOptions {
  jobId: string;
  onResumeUpdate?: (path: string | null, value: unknown) => void;
}

interface UseChatReturn {
  messages: ChatMessageRead[];
  isLoading: boolean;
  isSending: boolean;
  error: string | null;
  sendMessage: (
    content: string,
    sectionContext?: string | null,
    docId?: string | null,
    intentOverride?: string | null,
  ) => Promise<void>;
  clearMessages: () => Promise<void>;
  refreshMessages: () => Promise<void>;
}

export function useChat({ jobId, onResumeUpdate }: UseChatOptions): UseChatReturn {
  const [messages, setMessages] = useState<ChatMessageRead[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onResumeUpdateRef = useRef(onResumeUpdate);
  onResumeUpdateRef.current = onResumeUpdate;
  const mountedRef = useRef(true);

  // Track mount state to avoid setState on unmounted component
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const refreshMessages = useCallback(async () => {
    setIsLoading(true);
    try {
      const msgs = await fetchChatMessages(jobId);
      setMessages(msgs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load messages");
    } finally {
      setIsLoading(false);
    }
  }, [jobId]);

  // Load messages on mount
  useEffect(() => {
    refreshMessages();
  }, [refreshMessages]);

  const sendMessage = useCallback(
    async (
      content: string,
      sectionContext?: string | null,
      docId?: string | null,
      intentOverride?: string | null,
    ) => {
      setIsSending(true);
      setError(null);

      // Optimistically add user message
      const tempUserMsg: ChatMessageRead = {
        id: `temp-${Date.now()}`,
        job_id: jobId,
        role: "user",
        content,
        section_context: sectionContext ?? null,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, tempUserMsg]);

      try {
        const apiUrl = getApiUrl();
        const response = await fetch(`${apiUrl}/api/jobs/${jobId}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            content,
            section_context: sectionContext,
            doc_id: docId ?? undefined,
            intent_override: intentOverride ?? undefined,
          }),
        });

        if (!response.ok) {
          const errBody = await response.json().catch(() => ({}));
          throw new Error(errBody.detail || `Error: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error("No response stream");

        const decoder = new TextDecoder();
        let assistantText = "";
        let buffer = "";
        // Persist eventType across read iterations so split chunks work
        let eventType = "";

        // Add a placeholder assistant message
        const tempAssistantId = `temp-assistant-${Date.now()}`;
        setMessages((prev) => [
          ...prev,
          {
            id: tempAssistantId,
            job_id: jobId,
            role: "assistant",
            content: "",
            section_context: null,
            created_at: new Date().toISOString(),
          },
        ]);

        const processLines = (lines: string[]) => {
          for (const line of lines) {
            if (line.startsWith("event: ")) {
              eventType = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              const data = line.slice(6);
              try {
                const parsed = JSON.parse(data);

                if (eventType === "token") {
                  // Accumulate token text
                  assistantText += parsed.text ?? "";
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === tempAssistantId
                        ? { ...m, content: assistantText }
                        : m
                    )
                  );
                } else if (eventType === "section_update") {
                  onResumeUpdateRef.current?.(parsed.path, parsed.value);
                } else if (eventType === "done") {
                  if (parsed.message_id) {
                    setMessages((prev) =>
                      prev.map((m) =>
                        m.id === tempAssistantId
                          ? { ...m, id: parsed.message_id }
                          : m
                      )
                    );
                  }
                } else if (eventType === "error") {
                  setError(parsed.error || "Agent error");
                }
              } catch {
                // Skip unparseable data lines
              }
              // Reset eventType after consuming the data line
              eventType = "";
            }
            // Empty lines (SSE record separators) are just skipped
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // Split into complete lines; keep the last (possibly incomplete) line in buffer
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          // Only process if component is still mounted
          if (mountedRef.current) {
            processLines(lines);
          }
        }

        // Process any remaining data in the buffer after stream ends
        if (buffer.trim() && mountedRef.current) {
          processLines(buffer.split("\n"));
        }

        // If we unmounted during streaming, reload from DB on next mount
        // (the backend saved the response; refreshMessages() on mount picks it up)
      } catch (err) {
        if (mountedRef.current) {
          setError(err instanceof Error ? err.message : "Failed to send message");
          setMessages((prev) => prev.filter((m) => !m.id.startsWith("temp-assistant-")));
        }
      } finally {
        if (mountedRef.current) {
          setIsSending(false);
        }
      }
    },
    [jobId]
  );

  const clearMessages = useCallback(async () => {
    try {
      await clearChat(jobId);
      setMessages([]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clear chat");
    }
  }, [jobId]);

  return {
    messages,
    isLoading,
    isSending,
    error,
    sendMessage,
    clearMessages,
    refreshMessages,
  };
}
