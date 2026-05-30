"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  COLOR_PRESETS,
  DEFAULT_RESUME_DESIGN,
  FONT_PRESETS,
  ResumeJson,
  ResumeDesign,
  ProfileWorkHistory,
  ProfileAccomplishment,
  ProfileEducation,
  ProfilePublication,
  LAYOUT_DEFAULT_ORDER,
} from "@/lib/types";
import { PastVersionsDropdown } from "./past-versions-dropdown";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

interface ResumeEditorProps {
  docId: string;
  resumeJson: ResumeJson;
  selectedSection: string | null;
  recentlyUpdatedPaths: Set<string>;
  onSelectSection: (path: string | null) => void;
  onSectionEdit: (path: string, value: unknown) => void;
  onSwapResearch?: (index: number, accomplishmentId: string) => void;
  swappingIndex?: number | null;
  onAddResearch?: (accomplishmentId: string) => Promise<void> | void;
  addingResearch?: boolean;
  onGenerateBullet?: (employerKey: string, accomplishmentId: string) => Promise<void> | void;
  generatingBullet?: { employer_key: string; accomplishment_id: string } | null;
  workHistory?: ProfileWorkHistory[];
  accomplishments?: ProfileAccomplishment[];
  education?: ProfileEducation[];
  profilePublications?: ProfilePublication[];
  resumeDesign?: ResumeDesign;
}

function employerToKey(employer: string): string {
  return employer
    .toLowerCase()
    .replace(/^the\s+/, "")
    .replace(/[^\w\s]/g, " ")
    .trim()
    .replace(/\s+/g, "_");
}

function employerKeyToLabel(key: string, workHistory?: ProfileWorkHistory[]): string {
  if (!workHistory) return key;
  for (const wh of workHistory) {
    if (employerToKey(wh.employer) === key) return wh.employer;
  }
  return key;
}

// ---------------------------------------------------------------------------
// Editable text component
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
// Preview color types
// ---------------------------------------------------------------------------

interface PreviewColors {
  navy: string;
  orange: string;
}

function SectionHeader({
  children,
  colors,
  showUnderline = true,
}: {
  children: React.ReactNode;
  colors: PreviewColors;
  showUnderline?: boolean;
}) {
  return (
    <h2
      className={`text-xs font-bold tracking-wider uppercase mt-4 mb-1.5 pb-0.5 ${
        showUnderline ? "border-b" : ""
      }`}
      style={{ color: colors.navy, borderColor: colors.orange }}
    >
      {children}
    </h2>
  );
}

// ---------------------------------------------------------------------------
// Sortable section wrapper — drag handle appears on hover beside the header
// ---------------------------------------------------------------------------

function SortableSection({ id, children }: { id: string; children: React.ReactNode }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id });
  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
      }}
      className="group/section relative"
    >
      <div
        className="absolute -left-5 top-4 opacity-0 group-hover/section:opacity-100
                   transition-opacity cursor-grab text-gray-300 hover:text-gray-500 select-none z-10"
        {...attributes}
        {...listeners}
        aria-label="Drag to reorder section"
      >
        ⠿
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main editor
// ---------------------------------------------------------------------------

export function ResumeEditor({
  docId,
  resumeJson,
  selectedSection,
  recentlyUpdatedPaths,
  onSelectSection,
  onSectionEdit,
  onSwapResearch,
  swappingIndex,
  onAddResearch,
  addingResearch,
  onGenerateBullet,
  generatingBullet,
  workHistory,
  accomplishments,
  education,
  profilePublications,
  resumeDesign,
}: ResumeEditorProps) {
  const design = resumeDesign ?? DEFAULT_RESUME_DESIGN;

  const previewColors = useMemo<PreviewColors>(() => {
    const scheme = COLOR_PRESETS.find((c) => c.id === design.color_scheme) ?? COLOR_PRESETS[0];
    return { navy: scheme.body, orange: scheme.accent };
  }, [design.color_scheme]);

  const previewFont = useMemo(() => {
    const font = FONT_PRESETS.find((f) => f.id === design.font) ?? FONT_PRESETS[0];
    return font.fontFamily;
  }, [design.font]);

  const showSectionUnderline =
    design.layout !== "banner" && design.layout !== "compact";

  const handleSave = useCallback(
    (path: string, value: unknown) => {
      onSectionEdit(path, value);
    },
    [onSectionEdit]
  );

  const isSelected = (path: string) => selectedSection === path;

  const isUpdated = (path: string) => {
    for (const updated of recentlyUpdatedPaths) {
      if (path === updated || path.startsWith(updated + ".") || updated.startsWith(path + ".")) {
        return true;
      }
    }
    return false;
  };

  // --- Section ordering ---
  const effectiveOrder = useMemo(() => {
    const layout = design.layout ?? "banner";
    return resumeJson.section_order ?? LAYOUT_DEFAULT_ORDER[layout] ?? LAYOUT_DEFAULT_ORDER["banner"];
  }, [resumeJson.section_order, design.layout]);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      const oldIdx = effectiveOrder.indexOf(active.id as string);
      const newIdx = effectiveOrder.indexOf(over.id as string);
      onSectionEdit("section_order", arrayMove([...effectiveOrder], oldIdx, newIdx));
    }
  }

  function renderSection(id: string): React.ReactNode {
    switch (id) {
      case "summary": return (
        <>
          <SectionHeader colors={previewColors} showUnderline={showSectionUnderline}>Summary</SectionHeader>
          <EditableText
            value={resumeJson.summary}
            path="summary"
            isSelected={isSelected("summary")}
            isRecentlyUpdated={isUpdated("summary")}
            onSelect={onSelectSection}
            onSave={handleSave}
            multiline
          />
          <div className="mt-0.5" onClick={(e) => e.stopPropagation()}>
            <PastVersionsDropdown
              docId={docId}
              entityType="summary"
              entityKey="__scalar__"
              applyPath="summary"
              onApply={handleSave}
            />
          </div>
        </>
      );

      case "selected_research": return (resumeJson.selected_research?.length > 0 || onAddResearch) ? (
        <>
          <SectionHeader colors={previewColors} showUnderline={showSectionUnderline}>Selected Research</SectionHeader>
          <div className="space-y-2">
            {(resumeJson.selected_research || []).map((entry, i) => {
              const currentIds = new Set(resumeJson.selected_research.map((r) => r.accomplishment_id));
              const availableAccomplishments = (accomplishments || []).filter(
                (a) => a.id && (!currentIds.has(a.id) || a.id === entry.accomplishment_id)
              );
              const isSwapping = swappingIndex === i;
              const allEntries = resumeJson.selected_research || [];
              return (
                <div key={i} className="space-y-0.5 group/research">
                  <div className="flex items-baseline gap-1">
                    <EditableText
                      value={entry.category_label}
                      path={`selected_research.${i}.category_label`}
                      isSelected={isSelected(`selected_research.${i}.category_label`) || isSelected(`selected_research.${i}`)}
                      isRecentlyUpdated={isUpdated(`selected_research.${i}.category_label`)}
                      onSelect={onSelectSection}
                      onSave={handleSave}
                      className="text-xs font-bold uppercase tracking-wider"
                      style={{ color: previewColors.orange }}
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
                    <div className="flex items-center gap-0.5 opacity-0 group-hover/research:opacity-100 transition-opacity shrink-0">
                      <div className="flex flex-col">
                        {i > 0 && (
                          <button
                            type="button"
                            className="text-gray-400 hover:text-gray-700 p-0 leading-none text-[10px]"
                            title="Move up"
                            onClick={(e) => {
                              e.stopPropagation();
                              const next = [...allEntries];
                              [next[i - 1], next[i]] = [next[i], next[i - 1]];
                              onSectionEdit("selected_research", next);
                            }}
                          >▲</button>
                        )}
                        {i < allEntries.length - 1 && (
                          <button
                            type="button"
                            className="text-gray-400 hover:text-gray-700 p-0 leading-none text-[10px]"
                            title="Move down"
                            onClick={(e) => {
                              e.stopPropagation();
                              const next = [...allEntries];
                              [next[i], next[i + 1]] = [next[i + 1], next[i]];
                              onSectionEdit("selected_research", next);
                            }}
                          >▼</button>
                        )}
                      </div>
                      <button
                        type="button"
                        className="text-gray-400 hover:text-red-500 p-0 leading-none text-[10px] ml-0.5"
                        title="Delete entry"
                        onClick={(e) => {
                          e.stopPropagation();
                          const next = allEntries.filter((_, idx) => idx !== i);
                          onSectionEdit("selected_research", next);
                        }}
                      >✕</button>
                    </div>
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
                  {onSwapResearch && availableAccomplishments.length > 0 && (
                    <div className="flex items-center gap-1.5 pt-0.5 flex-wrap" onClick={(e) => e.stopPropagation()}>
                      <select
                        className="text-[11px] text-gray-500 bg-transparent border border-gray-200 rounded px-1.5 py-0.5 cursor-pointer hover:border-gray-400 focus:outline-none focus:ring-1 focus:ring-amber-300 max-w-[300px] truncate"
                        value={entry.accomplishment_id || ""}
                        disabled={isSwapping}
                        onChange={(e) => {
                          const newId = e.target.value;
                          if (newId && newId !== entry.accomplishment_id) {
                            onSwapResearch(i, newId);
                          }
                        }}
                      >
                        <option value={entry.accomplishment_id || ""}>{entry.title}</option>
                        {availableAccomplishments
                          .filter((a) => a.id !== entry.accomplishment_id)
                          .map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.title}{a.employer ? ` (${a.employer})` : ""}
                            </option>
                          ))}
                      </select>
                      {entry.accomplishment_id && (
                        <PastVersionsDropdown
                          docId={docId}
                          entityType="research_description"
                          entityKey={entry.accomplishment_id}
                          applyPath={`selected_research.${i}.description`}
                          onApply={handleSave}
                        />
                      )}
                      {isSwapping && (
                        <span className="text-[11px] text-amber-600 animate-pulse">generating...</span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {onAddResearch && (() => {
              const currentIds = new Set(
                (resumeJson.selected_research || []).map((r) => r.accomplishment_id),
              );
              const available = (accomplishments || []).filter(
                (a) => a.id && !currentIds.has(a.id),
              );
              if (available.length === 0) return null;
              return (
                <div className="pt-1" onClick={(e) => e.stopPropagation()}>
                  <select
                    className="text-[11px] text-blue-600 bg-transparent border border-dashed border-blue-300 rounded px-2 py-1 cursor-pointer hover:bg-blue-50 focus:outline-none focus:ring-1 focus:ring-blue-300 max-w-[340px] truncate disabled:opacity-50 disabled:cursor-not-allowed"
                    value=""
                    disabled={!!addingResearch}
                    onChange={(e) => {
                      const id = e.target.value;
                      if (id && onAddResearch) onAddResearch(id);
                      e.target.value = "";
                    }}
                  >
                    <option value="">
                      {addingResearch ? "Generating new entry…" : "+ Add research from accomplishment"}
                    </option>
                    {available.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.title}{a.employer ? ` (${a.employer})` : ""}
                      </option>
                    ))}
                  </select>
                </div>
              );
            })()}
          </div>
        </>
      ) : null;

      case "experience": return resumeJson.experience ? (() => {
        const entries = Object.entries(resumeJson.experience);
        if (workHistory && workHistory.length > 0) {
          const order = new Map<string, number>();
          workHistory.forEach((wh, idx) => {
            order.set(employerToKey(wh.employer), idx);
          });
          entries.sort(([a], [b]) => {
            const ai = order.has(a) ? order.get(a)! : Number.MAX_SAFE_INTEGER;
            const bi = order.has(b) ? order.get(b)! : Number.MAX_SAFE_INTEGER;
            return ai - bi;
          });
        }
        return (
          <>
            <SectionHeader colors={previewColors} showUnderline={showSectionUnderline}>Professional Experience</SectionHeader>
            {entries.map(([employer, data]) => {
              const bulletsPath = `experience.${employer}.bullets`;
              return (
                <div key={employer} className="mb-3 group/employer">
                  <div className="flex items-center gap-1">
                    <div
                      className={`cursor-pointer rounded px-1 -mx-1 text-xs font-semibold mb-1 flex-1 ${
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
                      style={{ color: previewColors.navy }}
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectSection(bulletsPath);
                      }}
                    >
                      {employerKeyToLabel(employer, workHistory)}
                    </div>
                    {Object.keys(resumeJson.experience).length > 1 && (
                      <button
                        className="text-gray-300 hover:text-red-500 p-0 leading-none text-[10px] opacity-0 group-hover/employer:opacity-100 transition-opacity"
                        title="Remove experience"
                        onClick={(e) => {
                          e.stopPropagation();
                          const newExperience = { ...resumeJson.experience };
                          delete newExperience[employer];
                          onSectionEdit("experience", newExperience);
                        }}
                      >✕</button>
                    )}
                  </div>
                  <ul className="list-disc list-outside ml-4 space-y-0.5">
                    {data.bullets.map((bullet, bi) => {
                      const bulletPath = `experience.${employer}.bullets.${bi}`;
                      const bulletText = typeof bullet === "string" ? bullet : bullet.text;
                      return (
                        <li key={bi} className="group/bullet relative">
                          <div className="flex items-start gap-0.5">
                            <EditableText
                              value={bulletText}
                              path={`${bulletPath}.text`}
                              isSelected={isSelected(bulletPath) || isSelected(`${bulletPath}.text`) || isSelected(bulletsPath)}
                              isRecentlyUpdated={isUpdated(bulletPath) || isUpdated(`${bulletPath}.text`)}
                              onSelect={onSelectSection}
                              onSave={handleSave}
                              multiline
                              className="text-xs flex-1"
                            />
                            <div className="flex items-center gap-0.5 opacity-0 group-hover/bullet:opacity-100 transition-opacity shrink-0 pt-0.5">
                              <div className="flex flex-col">
                                {bi > 0 && (
                                  <button
                                    className="text-gray-400 hover:text-gray-700 p-0 leading-none text-[10px]"
                                    title="Move up"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      const newBullets = [...data.bullets];
                                      [newBullets[bi - 1], newBullets[bi]] = [newBullets[bi], newBullets[bi - 1]];
                                      onSectionEdit(bulletsPath, newBullets);
                                    }}
                                  >▲</button>
                                )}
                                {bi < data.bullets.length - 1 && (
                                  <button
                                    className="text-gray-400 hover:text-gray-700 p-0 leading-none text-[10px]"
                                    title="Move down"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      const newBullets = [...data.bullets];
                                      [newBullets[bi], newBullets[bi + 1]] = [newBullets[bi + 1], newBullets[bi]];
                                      onSectionEdit(bulletsPath, newBullets);
                                    }}
                                  >▼</button>
                                )}
                              </div>
                              {data.bullets.length > 1 && (
                                <button
                                  className="text-gray-400 hover:text-red-500 p-0 leading-none text-[10px] ml-0.5"
                                  title="Delete bullet"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    const newBullets = data.bullets.filter((_, i) => i !== bi);
                                    onSectionEdit(bulletsPath, newBullets);
                                  }}
                                >✕</button>
                              )}
                            </div>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                  <div className="flex items-center gap-2 ml-4 mt-0.5 flex-wrap" onClick={(e) => e.stopPropagation()}>
                    <button
                      className="text-[10px] text-muted-foreground/50 hover:text-muted-foreground transition-colors"
                      onClick={(e) => {
                        e.stopPropagation();
                        const newBullets = [...data.bullets, { text: "New bullet — double-click to edit", accomplishment_ids: [] }];
                        onSectionEdit(bulletsPath, newBullets);
                      }}
                    >+ Add bullet</button>
                    {onGenerateBullet && (() => {
                      const usedIds = new Set<string>();
                      for (const b of data.bullets) {
                        if (typeof b !== "string") {
                          for (const id of b.accomplishment_ids || []) usedIds.add(id);
                        }
                      }
                      const candidates = (accomplishments || []).filter((a) => {
                        if (!a.id || usedIds.has(a.id)) return false;
                        if (a.work_history_key && a.work_history_key === employer) return true;
                        if (a.employer && employerToKey(a.employer) === employer) return true;
                        return false;
                      });
                      if (candidates.length === 0) return null;
                      const inFlight = generatingBullet && generatingBullet.employer_key === employer;
                      return (
                        <select
                          className="text-[10px] text-muted-foreground/60 hover:text-muted-foreground bg-transparent border border-dashed border-gray-300 rounded px-1 py-0.5 cursor-pointer disabled:opacity-50"
                          value=""
                          disabled={Boolean(inFlight)}
                          title="Generate a new bullet from one of this employer's accomplishments"
                          onChange={async (e) => {
                            const accId = e.target.value;
                            e.target.value = "";
                            if (!accId) return;
                            try { await onGenerateBullet(employer, accId); } catch { }
                          }}
                        >
                          <option value="">
                            {inFlight ? "Generating…" : `+ Add from accomplishment (${candidates.length})…`}
                          </option>
                          {candidates.map((a) => (
                            <option key={a.id} value={a.id}>{a.title?.slice(0, 80) ?? a.id}</option>
                          ))}
                        </select>
                      );
                    })()}
                    <PastVersionsDropdown
                      docId={docId}
                      entityType="experience_bullets_set"
                      entityKey={employer}
                      applyPath={bulletsPath}
                      onApply={handleSave}
                    />
                  </div>
                </div>
              );
            })}
            {workHistory && (() => {
              const currentKeys = new Set(Object.keys(resumeJson.experience));
              const available = workHistory.filter(wh => !currentKeys.has(employerToKey(wh.employer)));
              if (available.length === 0) return null;
              return (
                <select
                  className="text-[10px] text-muted-foreground/60 hover:text-muted-foreground mt-1 bg-transparent border border-dashed border-gray-300 rounded px-1 py-0.5 cursor-pointer"
                  value=""
                  onChange={(e) => {
                    const empName = e.target.value;
                    if (!empName) return;
                    const key = employerToKey(empName);
                    const newExperience = {
                      ...resumeJson.experience,
                      [key]: { bullets: [{ text: "New bullet — double-click to edit", accomplishment_ids: [] }] },
                    };
                    onSectionEdit("experience", newExperience);
                  }}
                >
                  <option value="">+ Add experience...</option>
                  {available.map(wh => (
                    <option key={wh.employer} value={wh.employer}>{wh.employer}</option>
                  ))}
                </select>
              );
            })()}
          </>
        );
      })() : null;

      case "publications": return ((resumeJson.publications?.length ?? 0) > 0 || (profilePublications?.length ?? 0) > 0) ? (
        <>
          <SectionHeader colors={previewColors} showUnderline={showSectionUnderline}>Selected Publications</SectionHeader>
          <ul className="list-disc list-outside ml-4 space-y-0.5">
            {(resumeJson.publications ?? []).map((pub, i) => {
              const pubs = resumeJson.publications ?? [];
              return (
                <li key={i} className="group/pub">
                  <div className="flex items-start gap-0.5">
                    <EditableText
                      value={pub.citation}
                      path={`publications.${i}.citation`}
                      isSelected={isSelected(`publications.${i}.citation`) || isSelected("publications")}
                      isRecentlyUpdated={isUpdated(`publications.${i}.citation`)}
                      onSelect={onSelectSection}
                      onSave={(path, val) => handleSave(path, val)}
                      multiline
                      className="text-xs flex-1"
                    />
                    <div className="flex items-center gap-0.5 opacity-0 group-hover/pub:opacity-100 transition-opacity shrink-0 pt-0.5">
                      <div className="flex flex-col">
                        {i > 0 && (
                          <button
                            className="text-gray-400 hover:text-gray-700 p-0 leading-none text-[10px]"
                            title="Move up"
                            onClick={(e) => {
                              e.stopPropagation();
                              const next = [...pubs];
                              [next[i - 1], next[i]] = [next[i], next[i - 1]];
                              onSectionEdit("publications", next);
                            }}
                          >▲</button>
                        )}
                        {i < pubs.length - 1 && (
                          <button
                            className="text-gray-400 hover:text-gray-700 p-0 leading-none text-[10px]"
                            title="Move down"
                            onClick={(e) => {
                              e.stopPropagation();
                              const next = [...pubs];
                              [next[i], next[i + 1]] = [next[i + 1], next[i]];
                              onSectionEdit("publications", next);
                            }}
                          >▼</button>
                        )}
                      </div>
                      <button
                        className="text-gray-400 hover:text-red-500 p-0 leading-none text-[10px] ml-0.5"
                        title="Remove publication"
                        onClick={(e) => {
                          e.stopPropagation();
                          const next = pubs.filter((_, idx) => idx !== i);
                          onSectionEdit("publications", next);
                        }}
                      >✕</button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
          {profilePublications && profilePublications.length > 0 && (() => {
            const pubKey = (pp: ProfilePublication): string =>
              pp.id || pp.rand_id || pp.doi || pp.url || pp.title || "";
            const usedKeys = new Set(
              (resumeJson.publications ?? [])
                .map((p) => p.publication_id)
                .filter((x): x is string => Boolean(x)),
            );
            const available = profilePublications.filter(
              (pp) => pp.title && !usedKeys.has(pubKey(pp)),
            );
            if (available.length === 0) return null;
            return (
              <select
                className="text-[10px] text-muted-foreground/60 hover:text-muted-foreground mt-1 ml-4 bg-transparent border border-dashed border-gray-300 rounded px-1 py-0.5 cursor-pointer max-w-full"
                value=""
                onChange={(e) => {
                  const key = e.target.value;
                  if (!key) return;
                  const pp = profilePublications.find((p) => pubKey(p) === key);
                  if (!pp) return;
                  const authors = (pp.authors && pp.authors.length > 0) ? pp.authors.join(", ") + "." : "";
                  const title = pp.title ? `${pp.title}.` : "";
                  const venue = pp.venue || "";
                  const year = pp.year != null ? String(pp.year) : "";
                  const venueYear = [venue, year].filter(Boolean).join(", ");
                  const citation = [authors, title, venueYear ? `${venueYear}.` : ""].filter(Boolean).join(" ").trim();
                  const next = [...(resumeJson.publications ?? []), { citation, publication_id: key }];
                  onSectionEdit("publications", next);
                }}
              >
                <option value="">+ Add publication...</option>
                {available.map((pp) => {
                  const key = pubKey(pp);
                  const yr = pp.year != null ? `(${pp.year})` : "";
                  return (
                    <option key={key || pp.title} value={key}>
                      {[pp.title, yr].filter(Boolean).join(" ").slice(0, 100)}
                    </option>
                  );
                })}
              </select>
            );
          })()}
        </>
      ) : null;

      case "technical_skills": return resumeJson.technical_skills ? (
        <>
          <SectionHeader colors={previewColors} showUnderline={showSectionUnderline}>Technical Skills</SectionHeader>
          <div className="space-y-1">
            {(
              [
                ["ai_systems", "AI Systems"],
                ["data_science", "Data Science"],
                ["engineering", "Engineering"],
              ] as const
            ).map(([key, skillLabel]) => {
              const val = resumeJson.technical_skills[key];
              if (!val) return null;
              const path = `technical_skills.${key}`;
              return (
                <div key={key} className="flex gap-1 items-start">
                  <span className="text-xs font-semibold shrink-0" style={{ color: previewColors.navy }}>
                    {skillLabel}:
                  </span>
                  <div className="flex-1 min-w-0">
                    <EditableText
                      value={val}
                      path={path}
                      isSelected={isSelected(path) || isSelected("technical_skills")}
                      isRecentlyUpdated={isUpdated(path)}
                      onSelect={onSelectSection}
                      onSave={handleSave}
                      className="text-xs"
                    />
                    <div className="mt-0.5" onClick={(e) => e.stopPropagation()}>
                      <PastVersionsDropdown
                        docId={docId}
                        entityType="skill_bucket"
                        entityKey={key}
                        applyPath={path}
                        onApply={handleSave}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      ) : null;

      case "education": return (education && education.length > 0) ? (
        <>
          <SectionHeader colors={previewColors} showUnderline={showSectionUnderline}>Education</SectionHeader>
          <div className="space-y-0.5">
            {education.map((edu, i) => (
              <div key={i} className="text-xs">
                <span className="font-semibold" style={{ color: previewColors.navy }}>
                  {[edu.degree, edu.field].filter(Boolean).join(" ")}
                </span>
                {edu.institution && <span>, {edu.institution}</span>}
                {edu.year && <span> ({edu.year})</span>}
                {edu.honors && <span className="text-muted-foreground"> — {edu.honors}</span>}
              </div>
            ))}
          </div>
        </>
      ) : null;

      case "awards": return resumeJson.awards ? (
        <>
          <SectionHeader colors={previewColors} showUnderline={showSectionUnderline}>Awards & Honors</SectionHeader>
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
      ) : null;

      default: return null;
    }
  }

  return (
    <div
      className="text-sm leading-relaxed space-y-0"
      style={{ fontFamily: previewFont }}
      onClick={() => onSelectSection(null)}
    >
      {design.layout === "two_column" && (
        <div className="mb-3 text-[11px] bg-blue-50 border border-blue-200 rounded px-2.5 py-1.5 text-blue-900">
          Two-column layout selected. Preview is shown single-column for editing —
          download to see the actual two-column .docx.
        </div>
      )}

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
        <div className="mt-0.5 flex justify-center" onClick={(e) => e.stopPropagation()}>
          <PastVersionsDropdown
            docId={docId}
            entityType="tagline"
            entityKey="__scalar__"
            applyPath="tagline"
            onApply={handleSave}
          />
        </div>
      </div>

      {/* Sections — rendered in user-defined order via drag-and-drop */}
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={effectiveOrder} strategy={verticalListSortingStrategy}>
          {effectiveOrder.map((id) => (
            <SortableSection key={id} id={id}>
              {renderSection(id)}
            </SortableSection>
          ))}
        </SortableContext>
      </DndContext>
    </div>
  );
}