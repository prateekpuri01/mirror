"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ResumeJson } from "@/lib/types";

interface ResumeEditorProps {
  resumeJson: ResumeJson;
  selectedSection: string | null;
  recentlyUpdatedPaths: Set<string>;
  onSelectSection: (path: string | null) => void;
  onSectionEdit: (path: string, value: unknown) => void;
}

// ---------------------------------------------------------------------------
// Editable text component — handles inline editing with debounced save
// ---------------------------------------------------------------------------

function EditableText({
  value,
  path,
  isSelected,
  isRecentlyUpdated,
  onSelect,
  onSave,
  multiline,
  className = "",
  style,
}: {
  value: string;
  path: string;
  isSelected: boolean;
  isRecentlyUpdated?: boolean;
  onSelect: (path: string) => void;
  onSave: (path: string, value: string) => void;
  multiline?: boolean;
  className?: string;
  style?: React.CSSProperties;
}) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(value);
  const inputRef = useRef<HTMLTextAreaElement | HTMLInputElement>(null);

  useEffect(() => {
    setEditValue(value);
  }, [value]);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      // Auto-resize textarea
      if (multiline && inputRef.current instanceof HTMLTextAreaElement) {
        inputRef.current.style.height = "auto";
        inputRef.current.style.height = inputRef.current.scrollHeight + "px";
      }
    }
  }, [editing, multiline]);

  const handleBlur = () => {
    setEditing(false);
    if (editValue !== value) {
      onSave(path, editValue);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setEditValue(value);
      setEditing(false);
    }
    if (e.key === "Enter" && !e.shiftKey && !multiline) {
      e.preventDefault();
      (e.target as HTMLElement).blur();
    }
  };

  if (editing) {
    return multiline ? (
      <textarea
        ref={inputRef as React.RefObject<HTMLTextAreaElement>}
        value={editValue}
        onChange={(e) => {
          setEditValue(e.target.value);
          // Auto-resize
          e.target.style.height = "auto";
          e.target.style.height = e.target.scrollHeight + "px";
        }}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
        className={`w-full border border-blue-300 rounded px-2 py-1 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-400 ${className}`}
      />
    ) : (
      <input
        ref={inputRef as React.RefObject<HTMLInputElement>}
        type="text"
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
        className={`w-full border border-blue-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 ${className}`}
      />
    );
  }

  return (
    <div
      className={`cursor-pointer rounded px-1 -mx-1 ${
        isRecentlyUpdated
          ? "transition-all duration-1000 ring-1 ring-amber-300 bg-amber-50/70"
          : "transition-all duration-150"
      } ${
        isSelected
          ? "ring-2 ring-blue-400 bg-blue-50"
          : isRecentlyUpdated
            ? ""
            : "hover:bg-gray-50 border border-transparent hover:border-gray-200"
      } ${className}`}
      style={style}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(path);
      }}
      onDoubleClick={(e) => {
        e.stopPropagation();
        setEditing(true);
      }}
    >
      {value || <span className="text-gray-400 italic">Empty</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section header styling (navy, uppercase)
// ---------------------------------------------------------------------------

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h2
      className="text-xs font-bold tracking-wider uppercase mt-4 mb-1.5 pb-0.5 border-b"
      style={{ color: "#1F3864", borderColor: "#1F3864" }}
    >
      {children}
    </h2>
  );
}

// ---------------------------------------------------------------------------
// Main editor
// ---------------------------------------------------------------------------

export function ResumeEditor({
  resumeJson,
  selectedSection,
  recentlyUpdatedPaths,
  onSelectSection,
  onSectionEdit,
}: ResumeEditorProps) {
  const handleSave = useCallback(
    (path: string, value: unknown) => {
      onSectionEdit(path, value);
    },
    [onSectionEdit]
  );

  const isSelected = (path: string) => selectedSection === path;

  // Check if a path (or any of its ancestors/descendants) was recently updated
  const isUpdated = (path: string) => {
    for (const updated of recentlyUpdatedPaths) {
      if (path === updated || path.startsWith(updated + ".") || updated.startsWith(path + ".")) {
        return true;
      }
    }
    return false;
  };

  return (
    <div
      className="text-sm leading-relaxed space-y-0 font-[Calibri,sans-serif]"
      onClick={() => onSelectSection(null)}
    >
      {/* Tagline */}
      <div className="text-center mb-3">
        <EditableText
          value={resumeJson.tagline}
          path="tagline"
          isSelected={isSelected("tagline")}
          isRecentlyUpdated={isUpdated("tagline")}
          onSelect={onSelectSection}
          onSave={handleSave}
          className="text-sm font-medium"
        />
      </div>

      {/* Summary */}
      <SectionHeader>Summary</SectionHeader>
      <EditableText
        value={resumeJson.summary}
        path="summary"
        isSelected={isSelected("summary")}
        isRecentlyUpdated={isUpdated("summary")}
        onSelect={onSelectSection}
        onSave={handleSave}
        multiline
      />

      {/* Selected Research */}
      {resumeJson.selected_research?.length > 0 && (
        <>
          <SectionHeader>Selected Research</SectionHeader>
          <div className="space-y-2">
            {resumeJson.selected_research.map((entry, i) => (
              <div key={i} className="space-y-0.5">
                <div className="flex items-baseline gap-1">
                  <EditableText
                    value={entry.category_label}
                    path={`selected_research.${i}.category_label`}
                    isSelected={isSelected(`selected_research.${i}.category_label`) || isSelected(`selected_research.${i}`)}
                    isRecentlyUpdated={isUpdated(`selected_research.${i}.category_label`)}
                    onSelect={onSelectSection}
                    onSave={handleSave}
                    className="text-xs font-bold uppercase tracking-wider"
                    style={{ color: "#C45911" }}
                  />
                  <span className="text-xs text-gray-400">—</span>
                  <EditableText
                    value={entry.title}
                    path={`selected_research.${i}.title`}
                    isSelected={isSelected(`selected_research.${i}.title`) || isSelected(`selected_research.${i}`)}
                    isRecentlyUpdated={isUpdated(`selected_research.${i}.title`)}
                    onSelect={onSelectSection}
                    onSave={handleSave}
                    className="text-xs flex-1"
                  />
                </div>
                <EditableText
                  value={entry.description}
                  path={`selected_research.${i}.description`}
                  isSelected={isSelected(`selected_research.${i}.description`) || isSelected(`selected_research.${i}`)}
                  isRecentlyUpdated={isUpdated(`selected_research.${i}.description`)}
                  onSelect={onSelectSection}
                  onSave={handleSave}
                  multiline
                  className="text-xs text-gray-600"
                />
              </div>
            ))}
          </div>
        </>
      )}

      {/* Experience */}
      {resumeJson.experience && (
        <>
          <SectionHeader>Professional Experience</SectionHeader>
          {Object.entries(resumeJson.experience).map(([employer, data]) => {
            const empLabel: Record<string, string> = {
              rand: "RAND Corporation",
              finra: "FINRA",
              ucla: "UCLA Physics",
            };
            const bulletsPath = `experience.${employer}.bullets`;
            return (
              <div key={employer} className="mb-3">
                <div
                  className={`cursor-pointer rounded px-1 -mx-1 text-xs font-semibold mb-1 ${
                    isUpdated(bulletsPath)
                      ? "transition-all duration-1000 ring-1 ring-amber-300 bg-amber-50/70"
                      : "transition-all duration-150"
                  } ${
                    isSelected(`experience.${employer}`) || isSelected(bulletsPath)
                      ? "ring-2 ring-blue-400 bg-blue-50"
                      : isUpdated(bulletsPath)
                        ? ""
                        : "hover:bg-gray-50"
                  }`}
                  style={{ color: "#1F3864" }}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelectSection(bulletsPath);
                  }}
                >
                  {empLabel[employer] || employer}
                </div>
                <ul className="list-disc list-outside ml-4 space-y-0.5">
                  {data.bullets.map((bullet, bi) => {
                    const bulletPath = `experience.${employer}.bullets.${bi}`;
                    return (
                      <li key={bi}>
                        <EditableText
                          value={bullet}
                          path={bulletPath}
                          isSelected={isSelected(bulletPath) || isSelected(bulletsPath)}
                          isRecentlyUpdated={isUpdated(bulletPath)}
                          onSelect={onSelectSection}
                          onSave={handleSave}
                          multiline
                          className="text-xs"
                        />
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </>
      )}

      {/* Publications */}
      {resumeJson.publications?.length > 0 && (
        <>
          <SectionHeader>Selected Publications</SectionHeader>
          <ul className="list-disc list-outside ml-4 space-y-0.5">
            {resumeJson.publications.map((pub, i) => (
              <li key={i}>
                <EditableText
                  value={pub.citation}
                  path={`publications.${i}.citation`}
                  isSelected={isSelected(`publications.${i}.citation`) || isSelected("publications")}
                  isRecentlyUpdated={isUpdated(`publications.${i}.citation`)}
                  onSelect={onSelectSection}
                  onSave={(path, val) => handleSave(path, val)}
                  multiline
                  className="text-xs"
                />
              </li>
            ))}
          </ul>
        </>
      )}

      {/* Technical Skills */}
      {resumeJson.technical_skills && (
        <>
          <SectionHeader>Technical Skills</SectionHeader>
          <div className="space-y-1">
            {(
              [
                ["ai_systems", "AI Systems"],
                ["data_science", "Data Science"],
                ["engineering", "Engineering"],
                ["communication", "Communication"],
              ] as const
            ).map(([key, skillLabel]) => {
              const val = resumeJson.technical_skills[key];
              if (!val) return null;
              const path = `technical_skills.${key}`;
              return (
                <div key={key} className="flex gap-1">
                  <span className="text-xs font-semibold shrink-0" style={{ color: "#1F3864" }}>
                    {skillLabel}:
                  </span>
                  <EditableText
                    value={val}
                    path={path}
                    isSelected={isSelected(path) || isSelected("technical_skills")}
                    isRecentlyUpdated={isUpdated(path)}
                    onSelect={onSelectSection}
                    onSave={handleSave}
                    className="text-xs flex-1"
                  />
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Awards */}
      {resumeJson.awards && (
        <>
          <SectionHeader>Awards & Honors</SectionHeader>
          <EditableText
            value={resumeJson.awards}
            path="awards"
            isSelected={isSelected("awards")}
            isRecentlyUpdated={isUpdated("awards")}
            onSelect={onSelectSection}
            onSave={handleSave}
            className="text-xs"
          />
        </>
      )}
    </div>
  );
}
