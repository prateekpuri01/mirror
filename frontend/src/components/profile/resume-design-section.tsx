"use client";

import { useState } from "react";
import { AlertTriangle, Download, GripVertical, Loader2, Plus, RotateCcw, X } from "lucide-react";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  COLOR_PRESETS,
  DEFAULT_RESUME_DESIGN,
  FONT_PRESETS,
  LAYOUT_DEFAULT_ORDER,
  LAYOUT_PRESETS,
  SECTION_LABELS,
  type ResumeDesign,
} from "@/lib/types";
import { downloadResumeDesignSample } from "@/lib/api";

interface ResumeDesignSectionProps {
  data: ResumeDesign | undefined;
  onChange: (design: ResumeDesign) => void;
}

export function ResumeDesignSection({ data, onChange }: ResumeDesignSectionProps) {
  const design: ResumeDesign = data ?? DEFAULT_RESUME_DESIGN;
  const [sampleStatus, setSampleStatus] = useState<"idle" | "loading" | "error">("idle");
  const [sampleError, setSampleError] = useState<string | null>(null);

  const update = (patch: Partial<ResumeDesign>) => {
    onChange({ ...design, ...patch });
  };

  const handleDownloadSample = async () => {
    setSampleStatus("loading");
    setSampleError(null);
    try {
      const blob = await downloadResumeDesignSample(design);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `resume_sample_${design.layout}_${design.color_scheme}_${design.font}.docx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setSampleStatus("idle");
    } catch (err) {
      setSampleStatus("error");
      setSampleError(err instanceof Error ? err.message : "Download failed");
    }
  };

  const selectedLayout = LAYOUT_PRESETS.find((l) => l.id === design.layout);

  return (
    <div className="space-y-6">
      {/* Layout */}
      <div>
        <h3 className="text-sm font-semibold mb-2">Layout</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {LAYOUT_PRESETS.map((layout) => {
            const isSelected = layout.id === design.layout;
            return (
              <button
                key={layout.id}
                type="button"
                // Reset the section template when the layout changes — section
                // ids differ between layouts (e.g. timeline's experience_education),
                // so a stale order would be invalid for the new layout.
                onClick={() => update({ layout: layout.id, section_order: undefined })}
                className={`text-left border rounded-lg p-3 transition-all ${
                  isSelected
                    ? "border-foreground ring-2 ring-foreground/20 bg-foreground/[0.02]"
                    : "border-gray-200 hover:border-gray-400"
                }`}
              >
                <div className="flex items-start gap-3">
                  <LayoutThumbnail kind={layout.id} />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm">{layout.label}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {layout.description}
                    </div>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
        {selectedLayout?.atsWarning && (
          <div className="mt-2 flex items-start gap-2 text-xs bg-amber-50 border border-amber-200 rounded-md px-3 py-2 text-amber-900">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span>{selectedLayout.atsWarning}</span>
          </div>
        )}
      </div>

      {/* Color scheme */}
      <div>
        <h3 className="text-sm font-semibold mb-2">Color Scheme</h3>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
          {COLOR_PRESETS.map((scheme) => {
            const isSelected = scheme.id === design.color_scheme;
            return (
              <button
                key={scheme.id}
                type="button"
                onClick={() => update({ color_scheme: scheme.id })}
                className={`border rounded-md p-2 transition-all text-left ${
                  isSelected
                    ? "border-foreground ring-2 ring-foreground/20"
                    : "border-gray-200 hover:border-gray-400"
                }`}
              >
                <div className="flex items-center gap-1.5 mb-1">
                  <span
                    className="h-5 w-5 rounded-sm border border-black/10"
                    style={{ backgroundColor: scheme.accent }}
                  />
                  <span
                    className="h-3 w-3 rounded-sm border border-black/10"
                    style={{ backgroundColor: scheme.muted }}
                  />
                </div>
                <div className="text-xs font-medium leading-tight">{scheme.label}</div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Font */}
      <div>
        <h3 className="text-sm font-semibold mb-2">Font</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {FONT_PRESETS.map((font) => {
            const isSelected = font.id === design.font;
            return (
              <button
                key={font.id}
                type="button"
                onClick={() => update({ font: font.id })}
                className={`border rounded-md p-3 transition-all text-left ${
                  isSelected
                    ? "border-foreground ring-2 ring-foreground/20"
                    : "border-gray-200 hover:border-gray-400"
                }`}
              >
                <div
                  className="text-base leading-tight"
                  style={{ fontFamily: font.fontFamily }}
                >
                  Aa Bb Cc
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {font.label}{" "}
                  <span className="text-muted-foreground/70">· {font.category}</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Sections — reorder + show/hide. Sets resume_design.section_order,
          which becomes the template for job resumes you haven't manually
          reordered. */}
      <SectionsControl design={design} onChange={update} />

      {/* Download sample */}
      <div className="border-t pt-4">
        <div className="flex items-center justify-between gap-3">
          <div className="text-xs text-muted-foreground">
            Download a .docx rendered with these settings to see how it looks in Word.
            Uses your most recent generated resume if you have one, otherwise a
            sample profile.
          </div>
          <button
            type="button"
            onClick={handleDownloadSample}
            disabled={sampleStatus === "loading"}
            className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium border border-gray-300 rounded-md bg-white hover:bg-gray-50 disabled:opacity-60 disabled:cursor-wait shrink-0"
          >
            {sampleStatus === "loading" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            Download Sample
          </button>
        </div>
        {sampleStatus === "error" && sampleError && (
          <div className="mt-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-md px-3 py-2">
            {sampleError}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sections control — reorder (drag) and show/hide the resume's sections for
// the current layout. Edits design.section_order (the template).
// ---------------------------------------------------------------------------

function SectionsControl({
  design,
  onChange,
}: {
  design: ResumeDesign;
  onChange: (patch: Partial<ResumeDesign>) => void;
}) {
  const layoutOrder = LAYOUT_DEFAULT_ORDER[design.layout] ?? LAYOUT_DEFAULT_ORDER.banner;
  // Visible sections in their current order; hidden = in the layout but absent.
  const order = design.section_order ?? layoutOrder;
  const visible = order.filter((id) => layoutOrder.includes(id));
  const hidden = layoutOrder.filter((id) => !visible.includes(id));
  const isCustomized = design.section_order !== undefined;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      const oldIdx = visible.indexOf(active.id as string);
      const newIdx = visible.indexOf(over.id as string);
      onChange({ section_order: arrayMove(visible, oldIdx, newIdx) });
    }
  }

  return (
    <div className="border-t pt-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold">Sections</h3>
        {isCustomized && (
          <button
            type="button"
            onClick={() => onChange({ section_order: undefined })}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <RotateCcw className="h-3 w-3" />
            Reset to default
          </button>
        )}
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        Drag to reorder. Hidden sections won&apos;t appear in your resumes (the
        content is still generated). This sets the default for job resumes you
        haven&apos;t manually arranged.
      </p>

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={visible} strategy={verticalListSortingStrategy}>
          <div className="space-y-1.5">
            {visible.map((id) => (
              <SortableSectionRow
                key={id}
                id={id}
                label={SECTION_LABELS[id] ?? id}
                onHide={
                  // Keep at least one section visible.
                  visible.length > 1
                    ? () => onChange({ section_order: visible.filter((s) => s !== id) })
                    : undefined
                }
              />
            ))}
          </div>
        </SortableContext>
      </DndContext>

      {hidden.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-medium text-muted-foreground mb-1.5">Hidden</div>
          <div className="flex flex-wrap gap-1.5">
            {hidden.map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => onChange({ section_order: [...visible, id] })}
                className="inline-flex items-center gap-1 px-2 py-1 text-xs border border-dashed border-gray-300 rounded-md text-muted-foreground hover:border-gray-400 hover:text-foreground"
              >
                <Plus className="h-3 w-3" />
                {SECTION_LABELS[id] ?? id}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function SortableSectionRow({
  id,
  label,
  onHide,
}: {
  id: string;
  label: string;
  onHide?: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.5 : 1 }}
      className="flex items-center gap-2 border border-gray-200 rounded-md bg-white px-2 py-1.5"
    >
      <button
        type="button"
        className="cursor-grab text-gray-400 hover:text-gray-600 touch-none"
        aria-label={`Drag ${label}`}
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" />
      </button>
      <span className="flex-1 text-sm">{label}</span>
      {onHide && (
        <button
          type="button"
          onClick={onHide}
          aria-label={`Hide ${label}`}
          className="text-gray-400 hover:text-red-600 rounded p-0.5"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tiny SVG thumbnails for the layout cards — purely decorative.
// ---------------------------------------------------------------------------

function LayoutThumbnail({ kind }: { kind: string }) {
  const stroke = "#9CA3AF";
  const accent = "#4B5563";
  const common = {
    width: 44,
    height: 56,
    viewBox: "0 0 44 56",
    className: "shrink-0",
  } as const;
  switch (kind) {
    case "banner":
      return (
        <svg {...common}>
          <rect x="1" y="1" width="42" height="54" rx="2" fill="white" stroke={stroke} />
          {/* Filled colored band header */}
          <rect x="1" y="1" width="42" height="11" fill={accent} />
          <rect x="5" y="5" width="20" height="2" fill="white" />
          <rect x="5" y="8" width="14" height="1.2" fill="white" opacity="0.8" />
          {/* Body content */}
          <rect x="6" y="17" width="32" height="1.2" fill={stroke} />
          <rect x="6" y="21" width="28" height="1.2" fill={stroke} />
          <rect x="6" y="28" width="14" height="1.5" fill={accent} />
          <rect x="6" y="33" width="32" height="1.2" fill={stroke} />
          <rect x="6" y="37" width="30" height="1.2" fill={stroke} />
          <rect x="6" y="44" width="14" height="1.5" fill={accent} />
          <rect x="6" y="49" width="32" height="1.2" fill={stroke} />
        </svg>
      );
    case "compact":
      return (
        <svg {...common}>
          <rect x="1" y="1" width="42" height="54" rx="2" fill="white" stroke={stroke} />
          {/* Split header: name left, contact right */}
          <rect x="5" y="6" width="18" height="2.5" fill={accent} />
          <rect x="5" y="10" width="12" height="1.2" fill={stroke} />
          <rect x="28" y="6" width="10" height="1" fill={stroke} />
          <rect x="28" y="8.5" width="10" height="1" fill={stroke} />
          <rect x="28" y="11" width="10" height="1" fill={stroke} />
          {/* Thin rule under header */}
          <line x1="5" y1="15" x2="39" y2="15" stroke={accent} strokeWidth="0.5" />
          {/* Accent-bar section headers */}
          <rect x="5" y="19" width="1.5" height="2.5" fill={accent} />
          <rect x="8" y="19" width="14" height="1.8" fill={stroke} />
          <rect x="8" y="24" width="30" height="1.2" fill={stroke} />
          <rect x="8" y="28" width="28" height="1.2" fill={stroke} />
          <rect x="5" y="34" width="1.5" height="2.5" fill={accent} />
          <rect x="8" y="34" width="14" height="1.8" fill={stroke} />
          <rect x="8" y="39" width="30" height="1.2" fill={stroke} />
          <rect x="8" y="43" width="26" height="1.2" fill={stroke} />
          <rect x="8" y="47" width="28" height="1.2" fill={stroke} />
        </svg>
      );
    case "two_column":
      return (
        <svg {...common}>
          <rect x="1" y="1" width="42" height="54" rx="2" fill="white" stroke={stroke} />
          <rect x="6" y="6" width="32" height="3" fill={accent} />
          <line x1="18" y1="14" x2="18" y2="52" stroke={stroke} strokeDasharray="1 1" />
          <rect x="6" y="16" width="10" height="1.2" fill={stroke} />
          <rect x="6" y="20" width="10" height="1.2" fill={stroke} />
          <rect x="6" y="24" width="10" height="1.2" fill={stroke} />
          <rect x="6" y="30" width="10" height="1.2" fill={stroke} />
          <rect x="6" y="34" width="10" height="1.2" fill={stroke} />
          <rect x="20" y="16" width="18" height="1.2" fill={stroke} />
          <rect x="20" y="20" width="18" height="1.2" fill={stroke} />
          <rect x="20" y="24" width="16" height="1.2" fill={stroke} />
          <rect x="20" y="30" width="18" height="1.2" fill={stroke} />
          <rect x="20" y="34" width="16" height="1.2" fill={stroke} />
          <rect x="20" y="38" width="18" height="1.2" fill={stroke} />
        </svg>
      );
    case "timeline":
      return (
        <svg {...common}>
          <rect x="1" y="1" width="42" height="54" rx="2" fill="white" stroke={stroke} />
          {/* Dark designer band with centered name, hairline tagline rules, contact grid */}
          <rect x="1" y="1" width="42" height="18" fill={accent} />
          <rect x="12" y="4" width="20" height="2.5" fill="white" />
          <line x1="9" y1="9" x2="14" y2="9" stroke="white" strokeOpacity="0.5" strokeWidth="0.3" />
          <rect x="15" y="8.5" width="14" height="1" fill="white" opacity="0.85" />
          <line x1="30" y1="9" x2="35" y2="9" stroke="white" strokeOpacity="0.5" strokeWidth="0.3" />
          {/* Contact grid (3 cells) with vertical white separators */}
          <rect x="4" y="12" width="2" height="2" fill="white" opacity="0.85" />
          <rect x="3" y="15" width="6" height="1" fill="white" opacity="0.6" />
          <line x1="12" y1="11.5" x2="12" y2="17" stroke="white" strokeOpacity="0.3" strokeWidth="0.3" />
          <rect x="16" y="12" width="2" height="2" fill="white" opacity="0.85" />
          <rect x="14.5" y="15" width="7" height="1" fill="white" opacity="0.6" />
          <line x1="25" y1="11.5" x2="25" y2="17" stroke="white" strokeOpacity="0.3" strokeWidth="0.3" />
          <rect x="28" y="12" width="2" height="2" fill="white" opacity="0.85" />
          <rect x="26" y="15" width="8" height="1" fill="white" opacity="0.6" />
          {/* Body: section header, timeline with vertical rule, date gutter + content rows */}
          <rect x="6" y="22" width="12" height="1.4" fill={stroke} />
          <line x1="6" y1="24" x2="38" y2="24" stroke={accent} strokeOpacity="0.5" strokeWidth="0.3" />
          <line x1="17" y1="26" x2="17" y2="52" stroke={accent} strokeWidth="0.8" />
          <rect x="6" y="27" width="9" height="1" fill={stroke} />
          <rect x="19" y="27" width="18" height="1.2" fill={stroke} />
          <rect x="19" y="30" width="14" height="1" fill={stroke} opacity="0.7" />
          <rect x="19" y="33" width="17" height="1" fill={stroke} />
          <rect x="6" y="38" width="9" height="1" fill={stroke} />
          <rect x="19" y="38" width="18" height="1.2" fill={stroke} />
          <rect x="19" y="41" width="14" height="1" fill={stroke} opacity="0.7" />
          {/* Education sub-group */}
          <rect x="19" y="45" width="7" height="0.8" fill={accent} opacity="0.7" />
          <rect x="6" y="48" width="6" height="1" fill={stroke} />
          <rect x="19" y="48" width="16" height="1.1" fill={stroke} />
        </svg>
      );
    default:
      return <div className="h-14 w-11 bg-gray-100 rounded" />;
  }
}
