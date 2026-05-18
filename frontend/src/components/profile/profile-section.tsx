"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Check } from "lucide-react";

interface ProfileSectionProps {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  saveStatus?: "idle" | "saving" | "saved";
}

export function ProfileSection({
  title,
  children,
  defaultOpen = true,
  saveStatus = "idle",
}: ProfileSectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border rounded-lg bg-white">
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-gray-900 hover:bg-gray-50 transition-colors"
        onClick={() => setOpen(!open)}
      >
        <div className="flex items-center gap-2">
          {open ? (
            <ChevronDown className="h-4 w-4 text-gray-500" />
          ) : (
            <ChevronRight className="h-4 w-4 text-gray-500" />
          )}
          {title}
        </div>
        {saveStatus === "saving" && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground font-normal">
            <Loader2 className="h-3 w-3 animate-spin" />
            Saving...
          </span>
        )}
        {saveStatus === "saved" && (
          <span className="flex items-center gap-1 text-xs text-green-600 font-normal">
            <Check className="h-3 w-3" />
            Saved
          </span>
        )}
      </button>
      {open && <div className="px-4 pb-4 pt-1">{children}</div>}
    </div>
  );
}
