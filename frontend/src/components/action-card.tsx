"use client";

import { useState } from "react";
import { Check, Pencil, X } from "lucide-react";
import { ActionCardRead } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface ActionCardProps {
  card: ActionCardRead;
  onApply: () => void;
  onDismiss: () => void;
  onRefine: (refinement: string) => void;
  busy?: boolean;
}

function kindLabel(kind: string): string {
  switch (kind) {
    case "rewrite_section":
      return "Rewrite section";
    case "replace_selected_research":
      return "Swap research entry";
    case "add_bullet":
      return "Add bullet";
    case "remove_section":
      return "Remove";
    default:
      return kind.replaceAll("_", " ");
  }
}

export function ActionCard({ card, onApply, onDismiss, onRefine, busy }: ActionCardProps) {
  const [refining, setRefining] = useState(false);
  const [refinement, setRefinement] = useState("");

  const isResolved = card.status !== "pending";

  return (
    <div
      className={`my-2 rounded-md border text-xs ${
        card.status === "applied"
          ? "border-green-200 bg-green-50"
          : card.status === "dismissed"
          ? "border-gray-200 bg-gray-50 opacity-70"
          : "border-amber-200 bg-amber-50"
      }`}
    >
      <div className="flex items-center justify-between px-2.5 py-1.5 border-b border-current/10">
        <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">
          <span>Suggested edit</span>
          <span className="text-amber-400">·</span>
          <span className="font-normal normal-case tracking-normal text-amber-900">
            {kindLabel(card.kind)}
          </span>
          {card.section_path && (
            <>
              <span className="text-amber-400">·</span>
              <code className="font-mono text-[10px] text-amber-900">
                {card.section_path}
              </code>
            </>
          )}
        </div>
        {card.status === "applied" && (
          <span className="text-[10px] text-green-700 font-medium">Applied</span>
        )}
        {card.status === "dismissed" && (
          <span className="text-[10px] text-gray-500 font-medium">Dismissed</span>
        )}
      </div>

      <div className="px-2.5 py-2 space-y-2">
        {card.rationale && (
          <p className="text-[11px] italic text-amber-900/80">{card.rationale}</p>
        )}

        <div className="rounded bg-white/70 p-2 text-[11px] text-gray-800 whitespace-pre-wrap font-normal max-h-48 overflow-y-auto">
          {card.proposed_value}
        </div>

        {!isResolved && !refining && (
          <div className="flex items-center gap-1.5 pt-0.5">
            <Button
              size="sm"
              variant="default"
              className="h-7 px-2.5 text-[11px] bg-green-600 hover:bg-green-700"
              disabled={busy}
              onClick={onApply}
            >
              <Check className="h-3 w-3 mr-1" />
              Yes
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 px-2.5 text-[11px]"
              disabled={busy}
              onClick={onDismiss}
            >
              <X className="h-3 w-3 mr-1" />
              No
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2.5 text-[11px] text-amber-900 hover:text-amber-700"
              disabled={busy}
              onClick={() => setRefining(true)}
            >
              <Pencil className="h-3 w-3 mr-1" />
              Other
            </Button>
          </div>
        )}

        {refining && (
          <div className="space-y-1.5 pt-0.5">
            <Textarea
              value={refinement}
              onChange={(e) => setRefinement(e.target.value)}
              placeholder="What should be different? e.g. 'keep MUSE but tighten to 80 words'"
              className="resize-none min-h-[48px] text-[11px]"
              rows={2}
            />
            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                className="h-7 px-2.5 text-[11px]"
                disabled={busy || !refinement.trim()}
                onClick={() => {
                  onRefine(refinement.trim());
                  setRefining(false);
                  setRefinement("");
                }}
              >
                Send refinement
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2.5 text-[11px]"
                onClick={() => {
                  setRefining(false);
                  setRefinement("");
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
