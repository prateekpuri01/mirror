"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import { Send, Trash2, X, Zap } from "lucide-react";
import { ActionCardRead, ChatMessageRead, ProfileWorkHistory } from "@/lib/types";
import { ActionCard } from "@/components/action-card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Section label display (static entries + dynamic from work history)
// ---------------------------------------------------------------------------

function buildSectionDisplay(
  workHistory?: ProfileWorkHistory[],
  selectedResearchTitle?: string,
): Record<string, string> {
  const base: Record<string, string> = {
    tagline: "Tagline",
    summary: "Summary",
    selected_research: selectedResearchTitle || "Selected Research",
    publications: "Publications",
    technical_skills: "Technical Skills",
    "technical_skills.ai_systems": "AI Systems Skills",
    "technical_skills.data_science": "Data Science Skills",
    "technical_skills.engineering": "Engineering Skills",
    awards: "Awards",
  };

  if (workHistory) {
    for (const wh of workHistory) {
      const key = wh.employer
        .toLowerCase()
        .replace(/^the\s+/, "")
        .replace(/[^\w\s]/g, " ")
        .trim()
        .replace(/\s+/g, "_");
      base[`experience.${key}`] = `${wh.employer} Experience`;
      base[`experience.${key}.bullets`] = `${wh.employer} Bullets`;
    }
  }

  return base;
}

// Patterns for dynamic paths like "selected_research.0.category_label"
const SECTION_PATTERNS: [RegExp, string][] = [
  [/^selected_research\.(\d+)\.category_label$/, "Research Category"],
  [/^selected_research\.(\d+)\.title$/, "Research Title"],
  [/^selected_research\.(\d+)\.description$/, "Research Description"],
  [/^selected_research\.(\d+)$/, "Research Entry"],
  [/^experience\.(\w+)\.bullets\.(\d+)(?:\.text)?$/, "Bullet"],
  [/^publications\.(\d+)\.citation$/, "Publication"],
];

function getSectionLabel(path: string, sectionDisplay: Record<string, string>): string {
  // Try exact match first
  if (sectionDisplay[path]) return sectionDisplay[path];
  // Try regex patterns for dynamic paths
  for (const [pattern, label] of SECTION_PATTERNS) {
    if (pattern.test(path)) return label;
  }
  // Try partial matches (e.g. "experience.rand.bullets.2" -> "RAND Bullets")
  for (const [key, label] of Object.entries(sectionDisplay)) {
    if (path.startsWith(key)) return label;
  }
  return path;
}

// ---------------------------------------------------------------------------
// Renders an assistant message body, splicing in ActionCard components at the
// [[ACTION_CARD:<index>]] marker positions emitted by the brainstorm handler.
// ---------------------------------------------------------------------------

const ACTION_CARD_MARKER = /\[\[ACTION_CARD:(\d+)\]\]/g;

interface AssistantMessageBodyProps {
  content: string;
  cards: ActionCardRead[];
  onApplyCard: (cardId: string) => void;
  onDismissCard: (cardId: string) => void;
  onRefineCard: (cardId: string, refinement: string) => void;
  busyCardIds: Set<string>;
}

function AssistantMessageBody({
  content,
  cards,
  onApplyCard,
  onDismissCard,
  onRefineCard,
  busyCardIds,
}: AssistantMessageBodyProps) {
  const cardsByIndex = useMemo(() => {
    const map = new Map<number, ActionCardRead>();
    for (const c of cards) map.set(c.card_index, c);
    return map;
  }, [cards]);

  // If the message has no markers but does have cards (e.g. a re-render
  // after reload from DB where markers were already stripped), append the
  // cards at the bottom instead.
  if (!ACTION_CARD_MARKER.test(content) && cards.length > 0) {
    return (
      <div>
        <div className="whitespace-pre-wrap">{content}</div>
        {cards.map((card) => (
          <ActionCard
            key={card.id}
            card={card}
            onApply={() => onApplyCard(card.id)}
            onDismiss={() => onDismissCard(card.id)}
            onRefine={(r) => onRefineCard(card.id, r)}
            busy={busyCardIds.has(card.id)}
          />
        ))}
      </div>
    );
  }

  // Reset lastIndex (regex above was consumed by `.test`).
  ACTION_CARD_MARKER.lastIndex = 0;

  const segments: Array<{ type: "text" | "card"; value: string; idx?: number }> = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = ACTION_CARD_MARKER.exec(content)) !== null) {
    if (match.index > cursor) {
      segments.push({ type: "text", value: content.slice(cursor, match.index) });
    }
    segments.push({ type: "card", value: match[0], idx: Number(match[1]) });
    cursor = match.index + match[0].length;
  }
  if (cursor < content.length) {
    segments.push({ type: "text", value: content.slice(cursor) });
  }

  return (
    <div>
      {segments.map((seg, i) => {
        if (seg.type === "text") {
          const trimmed = seg.value.trim();
          if (!trimmed) return null;
          return (
            <div key={i} className="whitespace-pre-wrap">
              {seg.value.replace(/^\n+|\n+$/g, "")}
            </div>
          );
        }
        const card = seg.idx !== undefined ? cardsByIndex.get(seg.idx) : undefined;
        if (!card) return null;
        return (
          <ActionCard
            key={card.id}
            card={card}
            onApply={() => onApplyCard(card.id)}
            onDismiss={() => onDismissCard(card.id)}
            onRefine={(r) => onRefineCard(card.id, r)}
            busy={busyCardIds.has(card.id)}
          />
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat panel
// ---------------------------------------------------------------------------

interface ChatPanelProps {
  messages: ChatMessageRead[];
  actionCardsByMessage: Record<string, ActionCardRead[]>;
  isSending: boolean;
  isLoading: boolean;
  error: string | null;
  disabled: boolean;
  selectedSection: string | null;
  onSend: (
    content: string,
    sectionContext?: string | null,
    intentOverride?: string | null,
  ) => void;
  onProofread?: () => void;
  onApplyCard: (cardId: string) => void;
  onDismissCard: (cardId: string) => void;
  onRefineCard: (cardId: string, refinement: string) => void;
  busyCardIds: Set<string>;
  onClear: () => void;
  onDeselectSection: () => void;
  workHistory?: ProfileWorkHistory[];
  selectedResearchTitle?: string;
}

export function ChatPanel({
  messages,
  actionCardsByMessage,
  isSending,
  isLoading,
  error,
  disabled,
  selectedSection,
  onSend,
  onProofread,
  onApplyCard,
  onDismissCard,
  onRefineCard,
  busyCardIds,
  onClear,
  onDeselectSection,
  workHistory,
  selectedResearchTitle,
}: ChatPanelProps) {
  const sectionDisplay = useMemo(
    () => buildSectionDisplay(workHistory, selectedResearchTitle),
    [workHistory, selectedResearchTitle],
  );
  const [input, setInput] = useState("");
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  // Quick-edit is a one-shot modifier: when on, the next send bypasses the
  // brainstorm card flow and commits the edit directly via edit_section.
  // Resets after each send.
  const [quickEdit, setQuickEdit] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, messages[messages.length - 1]?.content]);

  const handleSend = () => {
    if (!input.trim() || disabled || isSending) return;
    onSend(input.trim(), selectedSection, quickEdit ? "quick_edit" : null);
    setInput("");
    setQuickEdit(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b shrink-0">
        <span className="text-xs font-semibold text-muted-foreground">Chat</span>
        <div className="flex items-center gap-1">
          {!disabled && !showClearConfirm && (
            <Button
              size="sm"
              variant={quickEdit ? "default" : "ghost"}
              className={`h-6 px-2 text-[11px] ${
                quickEdit
                  ? "bg-amber-500 hover:bg-amber-600 text-white"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              disabled={isSending}
              onClick={() => setQuickEdit((v) => !v)}
              title="Quick edit: bypass the action card and commit the next edit directly. One-shot — resets after send."
            >
              <Zap className="h-3 w-3 mr-1" />
              {quickEdit ? "Quick edit: next send" : "Quick edit"}
            </Button>
          )}
          {onProofread && !disabled && !showClearConfirm && (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[11px] text-muted-foreground hover:text-foreground"
              disabled={isSending}
              onClick={onProofread}
              title="Scan for typos, grammar, and awkward phrasing — no edits made"
            >
              Proofread
            </Button>
          )}
          {showClearConfirm ? (
            <div className="flex items-center gap-1">
              <span className="text-xs text-muted-foreground">Clear all?</span>
              <Button
                size="sm"
                variant="destructive"
                className="h-6 text-xs px-2"
                onClick={() => {
                  onClear();
                  setShowClearConfirm(false);
                }}
              >
                Yes
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 text-xs px-2"
                onClick={() => setShowClearConfirm(false)}
              >
                No
              </Button>
            </div>
          ) : (
            messages.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 w-6 p-0"
                onClick={() => setShowClearConfirm(true)}
                title="Clear chat"
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            )
          )}
        </div>
      </div>

      {/* Quick-edit banner (only when active) */}
      {quickEdit && !disabled && (
        <div className="px-3 py-1.5 bg-amber-50 border-b border-amber-200 text-[11px] text-amber-900">
          <strong>Quick edit armed</strong> — next message commits directly,
          no Yes/No card. Resets after send.
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2 min-h-0">
        {isLoading && messages.length === 0 && (
          <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">
            Loading...
          </div>
        )}

        {!isLoading && messages.length === 0 && !disabled && (
          <div className="flex items-center justify-center py-8 text-xs text-muted-foreground text-center">
            <p>Ask me to edit your resume, or click a section first to target it.</p>
          </div>
        )}

        {disabled && (
          <div className="flex items-center justify-center py-8 text-xs text-muted-foreground text-center">
            <p>Generate a resume to start chatting.</p>
          </div>
        )}

        {messages.map((msg) => {
          const cards = actionCardsByMessage[msg.id] || [];
          return (
            <div
              key={msg.id}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] rounded-lg px-3 py-1.5 text-xs leading-relaxed ${
                  msg.role === "user"
                    ? "bg-blue-600 text-white"
                    : "bg-gray-100 text-gray-800"
                }`}
              >
                {msg.section_context && msg.role === "user" && (
                  <div className="mb-1">
                    <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-blue-500 text-blue-100">
                      {getSectionLabel(msg.section_context, sectionDisplay)}
                    </Badge>
                  </div>
                )}
                {msg.role === "assistant" ? (
                  <AssistantMessageBody
                    content={msg.content}
                    cards={cards}
                    onApplyCard={onApplyCard}
                    onDismissCard={onDismissCard}
                    onRefineCard={onRefineCard}
                    busyCardIds={busyCardIds}
                  />
                ) : (
                  <div className="whitespace-pre-wrap">{msg.content}</div>
                )}
              </div>
            </div>
          );
        })}

        {isSending && (!messages.length || messages[messages.length - 1]?.content === "") && (
          <div className="flex justify-start items-center gap-2">
            <div className="bg-gray-100 rounded-lg px-3 py-2">
              <div className="flex items-center gap-1.5">
                <svg className="h-3 w-3 animate-spin text-gray-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                <span className="text-xs text-gray-500">Thinking...</span>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Error */}
      {error && (
        <div className="px-3 py-1.5 bg-red-50 text-red-700 text-xs border-t border-red-100">
          {error}
        </div>
      )}

      {/* Selected section badge */}
      {selectedSection && !disabled && (
        <div className="px-3 py-1.5 border-t bg-blue-50 flex items-center gap-2">
          <span className="text-[10px] text-blue-600 font-medium">Editing:</span>
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
            {getSectionLabel(selectedSection, sectionDisplay)}
          </Badge>
          <button
            onClick={onDeselectSection}
            type="button"
            title="Clear section scope — next message won't be anchored to this section"
            className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-blue-700 bg-blue-100 hover:bg-blue-200 hover:text-blue-900 transition-colors"
          >
            <X className="h-3 w-3" />
            Clear scope
          </button>
        </div>
      )}

      {/* Input area */}
      <div className="border-t px-3 py-2 shrink-0">
        <div className="flex gap-1.5">
          <Textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              disabled
                ? "Generate a resume first..."
                : quickEdit
                  ? selectedSection
                    ? `Quick-edit ${getSectionLabel(selectedSection, sectionDisplay)} — commits immediately`
                    : "Quick edit — commits immediately, no card"
                  : selectedSection
                    ? `Edit ${getSectionLabel(selectedSection, sectionDisplay)}, or ask about it...`
                    : "Ask, propose, or edit..."
            }
            disabled={disabled || isSending}
            className="resize-none min-h-[36px] max-h-[100px] text-xs flex-1"
            rows={1}
          />
          <Button
            size="sm"
            className="h-9 w-9 p-0 shrink-0"
            disabled={disabled || isSending || !input.trim()}
            onClick={handleSend}
          >
            <Send className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}
