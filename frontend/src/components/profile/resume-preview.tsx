"use client";

import { Fragment, useMemo } from "react";
import {
  COLOR_PRESETS,
  FONT_PRESETS,
  LAYOUT_DEFAULT_ORDER,
  type ResumeDesign,
  type ResumeJson,
} from "@/lib/types";

interface PreviewProfile {
  personal: Record<string, unknown>;
  education: { degree: string; field: string; institution: string; year: string; honors?: string }[];
  work_history: { employer: string; title: string; start: string; end?: string; location?: string }[];
}

interface ResumePreviewProps {
  design: ResumeDesign;
  resume: ResumeJson;
  profile: PreviewProfile;
  isSample?: boolean;
}

type Scheme = { accent: string; body: string; muted: string };

function employerToKey(employer: string): string {
  return employer
    .toLowerCase()
    .replace(/^the\s+/, "")
    .replace(/[^\w\s]/g, " ")
    .trim()
    .replace(/\s+/g, "_");
}

export function ResumePreview({ design, resume, profile, isSample }: ResumePreviewProps) {
  const scheme = useMemo(
    () => COLOR_PRESETS.find((c) => c.id === design.color_scheme) ?? COLOR_PRESETS[0],
    [design.color_scheme],
  );
  const fontFamily = useMemo(
    () => (FONT_PRESETS.find((f) => f.id === design.font) ?? FONT_PRESETS[0]).fontFamily,
    [design.font],
  );

  // Effective section order/visibility for this layout: the user's template
  // (design.section_order) wins; otherwise the layout default. Sections absent
  // from the list are hidden. Filtered to ids valid for the layout so a stale
  // template can't inject sections the layout doesn't render.
  const layoutDefault = LAYOUT_DEFAULT_ORDER[design.layout] ?? LAYOUT_DEFAULT_ORDER.banner;
  const order = (design.section_order ?? layoutDefault).filter((id) => layoutDefault.includes(id));

  const common = { resume, profile, scheme, fontFamily, isSample, order };

  if (design.layout === "two_column") return <TwoColumnPreview {...common} />;
  if (design.layout === "banner") return <BannerPreview {...common} />;
  if (design.layout === "compact") return <CompactPreview {...common} />;
  // timeline (and any unknown future ID) falls through to timeline preview
  return <TimelinePreview {...common} />;
}

// ---------------------------------------------------------------------------
// Shared building blocks
// ---------------------------------------------------------------------------

function SectionHeaderRule({
  children,
  scheme,
}: {
  children: React.ReactNode;
  scheme: Scheme;
}) {
  return (
    <h2
      className="text-[11px] font-bold tracking-wider uppercase mt-4 mb-1.5 pb-0.5 border-b"
      style={{ color: scheme.body, borderColor: scheme.accent }}
    >
      {children}
    </h2>
  );
}

function SectionHeaderSpaced({
  children,
  scheme,
}: {
  children: React.ReactNode;
  scheme: Scheme;
}) {
  // Letter-spaced uppercase, accent color, no rule.
  const text = typeof children === "string" ? children.toUpperCase().split("").join(" ") : children;
  return (
    <h2
      className="text-[11px] font-bold uppercase mt-4 mb-1.5"
      style={{ color: scheme.accent, letterSpacing: "0.18em" }}
    >
      {text}
    </h2>
  );
}

function SectionHeaderAccentBar({
  children,
  scheme,
}: {
  children: React.ReactNode;
  scheme: Scheme;
}) {
  return (
    <h2
      className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider mt-4 mb-1.5"
      style={{ color: scheme.body }}
    >
      <span
        aria-hidden="true"
        className="inline-block"
        style={{ width: "3px", height: "12px", backgroundColor: scheme.accent }}
      />
      {children}
    </h2>
  );
}

function Bullets({ items, color }: { items: string[]; color: string }) {
  return (
    <ul className="list-disc list-outside ml-4 space-y-0.5">
      {items.map((b, i) => (
        <li key={i} className="text-[11px] leading-snug" style={{ color }}>
          {b}
        </li>
      ))}
    </ul>
  );
}

function SampleBanner() {
  return (
    <div className="mb-2 text-[10px] bg-amber-50 border border-amber-200 rounded px-2 py-1 text-amber-900">
      Showing a sample resume. Once you generate your first tailored resume, the preview will use your real content.
    </div>
  );
}

function ContactLine({
  personal,
  className,
  color,
  align = "center",
}: {
  personal: { phone?: string; email?: string; linkedin?: string };
  className?: string;
  color: string;
  align?: "left" | "center" | "right";
}) {
  const justify = align === "left" ? "justify-start" : align === "right" ? "justify-end" : "justify-center";
  return (
    <div
      className={`text-[10px] flex ${justify} gap-3 flex-wrap ${className ?? ""}`}
      style={{ color }}
    >
      {personal.phone && <span>📱 {personal.phone}</span>}
      {personal.email && <span>✉ {personal.email}</span>}
      {personal.linkedin && <span>🌐 {personal.linkedin.replace(/^https?:\/\//, "")}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section body renderers (shared across layouts)
// ---------------------------------------------------------------------------

type SectionHeaderComponent = React.ComponentType<{ children: React.ReactNode; scheme: Scheme }>;

type SectionCtx = {
  resume: ResumeJson;
  profile: PreviewProfile;
  scheme: Scheme;
  Header: SectionHeaderComponent;
  experienceLabel?: string;
  ExperienceComponent?: React.ComponentType<{
    resume: ResumeJson;
    profile: PreviewProfile;
    scheme: Scheme;
  }>;
  // two_column renders the summary as a bare paragraph (no section header).
  summaryAsHeader?: boolean;
};

// Render a single resume section by id. Returns null when the section has no
// content. Shared by every layout preview so reorder/hide is honored uniformly.
function renderSection(id: string, ctx: SectionCtx): React.ReactNode {
  const {
    resume,
    profile,
    scheme,
    Header,
    experienceLabel = "Professional Experience",
    ExperienceComponent = ExperienceList,
    summaryAsHeader = true,
  } = ctx;

  switch (id) {
    case "summary":
      if (!resume.summary) return null;
      return summaryAsHeader ? (
        <section>
          <Header scheme={scheme}>Summary</Header>
          <p className="text-[11px]" style={{ color: scheme.body }}>{resume.summary}</p>
        </section>
      ) : (
        <p className="text-[11px] mb-2" style={{ color: scheme.body }}>{resume.summary}</p>
      );
    case "selected_research":
      if (!resume.selected_research?.length) return null;
      return (
        <section>
          <Header scheme={scheme}>{resume.selected_research_section_title || "Selected Research"}</Header>
          {resume.selected_research.map((entry, i) => (
            <div key={i} className="mb-1.5">
              <div className="text-[11px]">
                <span className="font-bold" style={{ color: scheme.accent }}>{entry.category_label?.toUpperCase()} — </span>
                <span className="font-semibold" style={{ color: scheme.body }}>{entry.title}</span>
              </div>
              {entry.description && (
                <p className="text-[11px] ml-3" style={{ color: scheme.body }}>{entry.description}</p>
              )}
            </div>
          ))}
        </section>
      );
    case "experience": {
      if (!resume.experience || !Object.keys(resume.experience).length) return null;
      const Experience = ExperienceComponent;
      return (
        <section>
          <Header scheme={scheme}>{experienceLabel}</Header>
          <Experience resume={resume} profile={profile} scheme={scheme} />
        </section>
      );
    }
    case "experience_education": {
      const hasTimeline =
        (resume.experience && Object.keys(resume.experience).length > 0) ||
        (profile.education?.length ?? 0) > 0;
      if (!hasTimeline) return null;
      return (
        <section>
          <Header scheme={scheme}>Experience &amp; Education</Header>
          <TimelineExperienceEducation resume={resume} profile={profile} scheme={scheme} />
        </section>
      );
    }
    case "publications":
      if (!resume.publications?.length) return null;
      return (
        <section>
          <Header scheme={scheme}>Selected Publications</Header>
          <Bullets items={resume.publications.map((p) => p.citation)} color={scheme.body} />
        </section>
      );
    case "technical_skills":
      if (!resume.technical_skills || !Object.keys(resume.technical_skills).length) return null;
      return (
        <section>
          <Header scheme={scheme}>Technical Skills</Header>
          <SkillsList skills={resume.technical_skills} scheme={scheme} />
        </section>
      );
    case "education":
      if (!profile.education?.length) return null;
      return (
        <section>
          <Header scheme={scheme}>Education</Header>
          <EducationList items={profile.education} scheme={scheme} />
        </section>
      );
    case "awards":
      if (!resume.awards) return null;
      return (
        <section>
          <Header scheme={scheme}>Awards &amp; Honors</Header>
          <p className="text-[11px]" style={{ color: scheme.body }}>{resume.awards}</p>
        </section>
      );
    default:
      return null;
  }
}

function ResumeSections({
  order,
  ...ctx
}: SectionCtx & { order: string[] }) {
  return (
    <>
      {order.map((id) => {
        const node = renderSection(id, ctx);
        return node ? <Fragment key={id}>{node}</Fragment> : null;
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// Banner: filled colored band header
// ---------------------------------------------------------------------------

function BannerPreview({
  resume,
  profile,
  scheme,
  fontFamily,
  isSample,
  order,
}: {
  resume: ResumeJson;
  profile: PreviewProfile;
  scheme: Scheme;
  fontFamily: string;
  isSample?: boolean;
  order: string[];
}) {
  const personal = (profile.personal || {}) as { name?: string; email?: string; phone?: string; linkedin?: string };
  return (
    <div
      className="text-[11px] leading-relaxed bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden"
      style={{ fontFamily, color: scheme.body }}
    >
      {isSample && (
        <div className="p-2 pb-0">
          <SampleBanner />
        </div>
      )}
      {/* Colored band */}
      <div className="px-6 py-4" style={{ backgroundColor: scheme.accent }}>
        <div className="text-2xl font-bold uppercase text-white tracking-wide">
          {personal.name}
        </div>
        {resume.tagline && (
          <div className="text-[11px] text-white/85 mt-0.5">{resume.tagline}</div>
        )}
      </div>
      <div className="px-6 pt-3 pb-5">
        <ContactLine personal={personal} color="#555" className="mb-1" />
        <ResumeSections
          order={order}
          resume={resume}
          profile={profile}
          scheme={scheme}
          Header={SectionHeaderSpaced}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compact: split header (name left, contact right) + accent-bar sections
// ---------------------------------------------------------------------------

function CompactPreview({
  resume,
  profile,
  scheme,
  fontFamily,
  isSample,
  order,
}: {
  resume: ResumeJson;
  profile: PreviewProfile;
  scheme: Scheme;
  fontFamily: string;
  isSample?: boolean;
  order: string[];
}) {
  const personal = (profile.personal || {}) as { name?: string; email?: string; phone?: string; linkedin?: string };
  return (
    <div
      className="text-[11px] leading-relaxed bg-white border border-gray-200 rounded-lg shadow-sm p-6"
      style={{ fontFamily, color: scheme.body }}
    >
      {isSample && <SampleBanner />}

      {/* Split header */}
      <div className="flex items-start justify-between gap-4 pb-2 border-b" style={{ borderColor: scheme.accent }}>
        <div>
          <div className="text-xl font-bold leading-tight" style={{ color: scheme.body }}>
            {personal.name}
          </div>
          {resume.tagline && (
            <div className="text-[11px] italic mt-0.5" style={{ color: scheme.accent }}>
              {resume.tagline}
            </div>
          )}
        </div>
        <div className="text-right text-[10px]" style={{ color: "#555" }}>
          {personal.phone && <div>📱 {personal.phone}</div>}
          {personal.email && <div>✉ {personal.email}</div>}
          {personal.linkedin && (
            <div>🌐 {personal.linkedin.replace(/^https?:\/\//, "")}</div>
          )}
        </div>
      </div>

      <ResumeSections
        order={order}
        resume={resume}
        profile={profile}
        scheme={scheme}
        Header={SectionHeaderAccentBar}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Timeline: centered header + date-gutter experience with vertical rule
// ---------------------------------------------------------------------------

function TimelinePreview({
  resume,
  profile,
  scheme,
  fontFamily,
  isSample,
  order,
}: {
  resume: ResumeJson;
  profile: PreviewProfile;
  scheme: Scheme;
  fontFamily: string;
  isSample?: boolean;
  order: string[];
}) {
  const personal = (profile.personal || {}) as {
    name?: string;
    email?: string;
    phone?: string;
    linkedin?: string;
    location?: string;
  };
  const grid = buildContactGrid(personal);

  return (
    <div
      className="text-[11px] leading-relaxed bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden"
      style={{ fontFamily, color: scheme.body }}
    >
      {isSample && (
        <div className="p-2 pb-0">
          <SampleBanner />
        </div>
      )}

      {/* Designer band header */}
      <div className="px-6 pt-5 pb-4 text-white" style={{ backgroundColor: scheme.accent }}>
        <div
          className="text-center text-2xl font-bold uppercase"
          style={{ letterSpacing: "0.12em" }}
        >
          {personal.name}
        </div>
        {resume.tagline && (
          <div className="flex items-center justify-center gap-2 mt-2 whitespace-nowrap">
            <span className="h-px w-8 bg-white/40" aria-hidden="true" />
            <span
              className="text-[8.5px] uppercase text-white/90"
              style={{ letterSpacing: "0.10em" }}
            >
              {resume.tagline}
            </span>
            <span className="h-px w-8 bg-white/40" aria-hidden="true" />
          </div>
        )}
        {grid.length > 0 && (
          <div
            className="grid mt-4 gap-0"
            style={{ gridTemplateColumns: `repeat(${grid.length}, 1fr)` }}
          >
            {grid.map((item, i) => (
              <div
                key={i}
                className={`text-center px-2 ${i > 0 ? "border-l border-white/25" : ""}`}
              >
                <div className="text-base leading-none mb-1">{item.icon}</div>
                <div className="text-[8px] text-white/85 break-all leading-tight">
                  {item.value}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Section order/visibility honored via the shared dispatcher. */}
      <div className="px-6 pt-3 pb-5">
        <ResumeSections
          order={order}
          resume={resume}
          profile={profile}
          scheme={scheme}
          Header={SectionHeaderRule}
        />
      </div>
    </div>
  );
}

function buildContactGrid(personal: {
  phone?: string;
  email?: string;
  linkedin?: string;
  location?: string;
}): { icon: string; value: string }[] {
  const items: { icon: string; value: string }[] = [];
  if (personal.location) items.push({ icon: "📍", value: personal.location });
  if (personal.phone) items.push({ icon: "📞", value: personal.phone });
  if (personal.email) items.push({ icon: "✉", value: personal.email });
  if (personal.linkedin) {
    const link = personal.linkedin.replace(/^https?:\/\//, "").replace(/^www\./, "");
    items.push({ icon: "🔗", value: link });
  }
  return items.slice(0, 4);
}

type TimelineRow = {
  date: string;
  title: string;
  subtitle: string;
  bullets: string[];
};

function TimelineExperienceEducation({
  resume,
  profile,
  scheme,
}: {
  resume: ResumeJson;
  profile: PreviewProfile;
  scheme: Scheme;
}) {
  const whByKey = new Map<string, PreviewProfile["work_history"][number]>();
  for (const wh of profile.work_history || []) {
    whByKey.set(employerToKey(wh.employer), wh);
  }
  const entries = Object.entries(resume.experience || {});
  if (profile.work_history?.length) {
    const order = new Map<string, number>();
    profile.work_history.forEach((wh, i) => order.set(employerToKey(wh.employer), i));
    entries.sort(([a], [b]) => (order.get(a) ?? Infinity) - (order.get(b) ?? Infinity));
  }

  const workItems: TimelineRow[] = entries.map(([key, block]) => {
    const wh = whByKey.get(key);
    return {
      date: wh ? `${wh.start} — ${wh.end || "Now"}` : "",
      title: wh?.title ?? key,
      subtitle: wh
        ? `${wh.employer}${wh.location ? `  ·  ${wh.location}` : ""}`
        : "",
      bullets: block.bullets.map((b) => (typeof b === "string" ? b : b.text)),
    };
  });

  const eduItems: TimelineRow[] = (profile.education || []).map((edu) => {
    const degreeField = `${edu.degree ?? ""} ${edu.field ?? ""}`.trim();
    const subtitle = edu.honors
      ? `${edu.institution}  —  ${edu.honors}`
      : edu.institution ?? "";
    return {
      date: String(edu.year ?? ""),
      title: degreeField,
      subtitle,
      bullets: [],
    };
  });

  return (
    <div className="mt-1">
      {workItems.map((item, i) => (
        <TimelineRowView key={`w-${i}`} item={item} scheme={scheme} />
      ))}
      {eduItems.length > 0 && (
        <div className="grid grid-cols-[72px_1fr]">
          <div />
          <div className="pl-3 pt-2 pb-1 border-l-2" style={{ borderColor: scheme.accent }}>
            <div className="text-[10px] italic font-semibold" style={{ color: scheme.muted }}>
              Education
            </div>
          </div>
        </div>
      )}
      {eduItems.map((item, i) => (
        <TimelineRowView key={`e-${i}`} item={item} scheme={scheme} />
      ))}
    </div>
  );
}

function TimelineRowView({ item, scheme }: { item: TimelineRow; scheme: Scheme }) {
  return (
    <div className="grid grid-cols-[72px_1fr]">
      <div
        className="text-[10px] pt-1 pr-2 font-semibold"
        style={{ color: scheme.body }}
      >
        {item.date}
      </div>
      <div className="pl-3 pb-3 border-l-2" style={{ borderColor: scheme.accent }}>
        <div className="text-[11px] font-semibold" style={{ color: scheme.body }}>
          {item.title}
        </div>
        {item.subtitle && (
          <div
            className="text-[10px] italic mb-0.5"
            style={{ color: scheme.muted }}
          >
            {item.subtitle}
          </div>
        )}
        {item.bullets.length > 0 && (
          <Bullets items={item.bullets} color={scheme.body} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Two-column preview — filled sidebar, white main. Mirrors the docx layout.
// ---------------------------------------------------------------------------

function TwoColumnPreview({
  resume,
  profile,
  scheme,
  fontFamily,
  isSample,
  order,
}: {
  resume: ResumeJson;
  profile: PreviewProfile;
  scheme: Scheme;
  fontFamily: string;
  isSample?: boolean;
  order: string[];
}) {
  const personal = (profile.personal || {}) as {
    name?: string;
    email?: string;
    phone?: string;
    linkedin?: string;
    location?: string;
  };
  const name = personal.name || "";
  const [firstName, ...rest] = name.split(" ");
  const lastName = rest.join(" ");

  // Sidebar skills are flattened from comma-separated technical_skills.
  const sidebarSkills = useMemo(() => {
    const out: string[] = [];
    const ts = resume.technical_skills || {};
    for (const v of [ts.ai_systems, ts.data_science, ts.engineering]) {
      if (!v) continue;
      for (const s of v.split(",").map((x) => x.trim())) {
        if (s) out.push(s);
      }
    }
    return out.slice(0, 12);
  }, [resume.technical_skills]);

  return (
    <div
      className="text-[11px] leading-relaxed bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden"
      style={{ fontFamily }}
    >
      {isSample && (
        <div className="p-2">
          <SampleBanner />
        </div>
      )}
      <div className="grid" style={{ gridTemplateColumns: "30% 70%" }}>
        <aside className="p-4 text-white" style={{ backgroundColor: scheme.accent }}>
          <div className="leading-tight mb-3">
            <div className="text-lg font-bold">{firstName}</div>
            <div className="text-lg font-bold">{lastName}</div>
          </div>

          <SidebarHeader>Contact</SidebarHeader>
          {personal.location && <SidebarItem label="Address" value={personal.location} />}
          {personal.phone && <SidebarItem label="Phone" value={personal.phone} />}
          {personal.email && <SidebarItem label="E-mail" value={personal.email} />}
          {personal.linkedin && (
            <SidebarItem
              label="LinkedIn"
              value={personal.linkedin.replace(/^https?:\/\//, "").replace(/^www\./, "")}
            />
          )}

          {sidebarSkills.length > 0 && (
            <>
              <SidebarHeader>Skills</SidebarHeader>
              <ul className="text-[10px] space-y-0.5 list-disc list-outside ml-3">
                {sidebarSkills.map((s, i) => (
                  <li key={i} className="text-white/85">{s}</li>
                ))}
              </ul>
            </>
          )}

          {profile.education?.length > 0 && (
            <>
              <SidebarHeader>Education</SidebarHeader>
              {profile.education.map((edu, i) => (
                <div key={i} className="mb-1.5">
                  <div className="text-[10px] font-semibold">
                    {`${edu.degree} ${edu.field}`.trim()}
                  </div>
                  <div className="text-[10px] text-white/75">
                    {[edu.institution, edu.year].filter(Boolean).join(", ")}
                  </div>
                </div>
              ))}
            </>
          )}
        </aside>

        <main className="p-5" style={{ color: scheme.body }}>
          {resume.tagline && (
            <p className="italic text-[11px] mb-2" style={{ color: scheme.body }}>
              {resume.tagline}
            </p>
          )}
          {/* Main-column sections in template order. technical_skills and
              education live in the sidebar, so they're never in this order. */}
          <ResumeSections
            order={order}
            resume={resume}
            profile={profile}
            scheme={scheme}
            Header={SectionHeaderRule}
            summaryAsHeader={false}
            experienceLabel="Work History"
            ExperienceComponent={TwoColumnExperience}
          />
        </main>
      </div>
    </div>
  );
}

function SidebarHeader({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[11px] font-bold uppercase tracking-wider mt-3 mb-1 pb-0.5 border-b border-white/30 text-white">
      {children}
    </h3>
  );
}

function SidebarItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="mb-1.5">
      <div className="text-[10px] font-semibold">{label}</div>
      <div className="text-[10px] text-white/85 break-words">{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Experience renderers
// ---------------------------------------------------------------------------

function ExperienceList({
  resume,
  profile,
  scheme,
}: {
  resume: ResumeJson;
  profile: PreviewProfile;
  scheme: Scheme;
}) {
  const whByKey = new Map<string, PreviewProfile["work_history"][number]>();
  for (const wh of profile.work_history || []) {
    whByKey.set(employerToKey(wh.employer), wh);
  }
  const entries = Object.entries(resume.experience || {});
  if (profile.work_history?.length) {
    const order = new Map<string, number>();
    profile.work_history.forEach((wh, i) => order.set(employerToKey(wh.employer), i));
    entries.sort(([a], [b]) => (order.get(a) ?? Infinity) - (order.get(b) ?? Infinity));
  }

  return (
    <>
      {entries.map(([key, block]) => {
        const wh = whByKey.get(key);
        return (
          <div key={key} className="mb-2">
            <div className="text-[11px] font-semibold" style={{ color: scheme.body }}>
              {wh?.employer ?? key}
              {wh?.location && <span className="font-normal" style={{ color: "#666" }}>{`  —  ${wh.location}`}</span>}
            </div>
            {wh && (
              <div className="text-[10px] italic mb-0.5" style={{ color: scheme.muted }}>
                {wh.title}{"  |  "}{wh.start} – {wh.end || "Present"}
              </div>
            )}
            <Bullets
              items={block.bullets.map((b) => (typeof b === "string" ? b : b.text))}
              color={scheme.body}
            />
          </div>
        );
      })}
    </>
  );
}

function TwoColumnExperience({
  resume,
  profile,
  scheme,
}: {
  resume: ResumeJson;
  profile: PreviewProfile;
  scheme: Scheme;
}) {
  const whByKey = new Map<string, PreviewProfile["work_history"][number]>();
  for (const wh of profile.work_history || []) {
    whByKey.set(employerToKey(wh.employer), wh);
  }
  const entries = Object.entries(resume.experience || {});
  if (profile.work_history?.length) {
    const order = new Map<string, number>();
    profile.work_history.forEach((wh, i) => order.set(employerToKey(wh.employer), i));
    entries.sort(([a], [b]) => (order.get(a) ?? Infinity) - (order.get(b) ?? Infinity));
  }

  return (
    <div className="space-y-2.5">
      {entries.map(([key, block]) => {
        const wh = whByKey.get(key);
        return (
          <div key={key} className="grid grid-cols-[60px_1fr] gap-2">
            <div className="text-[10px]" style={{ color: scheme.muted }}>
              {wh ? (
                <>
                  {wh.start}
                  <br />
                  – {wh.end || "Present"}
                </>
              ) : null}
            </div>
            <div>
              {wh && (
                <div className="text-[11px] font-bold" style={{ color: scheme.body }}>{wh.title}</div>
              )}
              <div className="text-[10px] italic mb-0.5" style={{ color: scheme.muted }}>
                {wh?.employer ?? key}
                {wh?.location && `, ${wh.location}`}
              </div>
              <Bullets
                items={block.bullets.map((b) => (typeof b === "string" ? b : b.text))}
                color={scheme.body}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skills + Education for single-column previews
// ---------------------------------------------------------------------------

function SkillsList({
  skills,
  scheme,
}: {
  skills: ResumeJson["technical_skills"];
  scheme: Scheme;
}) {
  const labels: Array<[keyof ResumeJson["technical_skills"], string]> = [
    ["ai_systems", "AI Systems"],
    ["data_science", "Data Science"],
    ["engineering", "Engineering"],
  ];
  return (
    <div className="space-y-0.5">
      {labels.map(([key, label]) => {
        const value = skills[key];
        if (!value) return null;
        return (
          <div key={String(key)} className="text-[11px]">
            <span className="font-bold" style={{ color: scheme.body }}>{label}: </span>
            <span style={{ color: scheme.body }}>{value}</span>
          </div>
        );
      })}
    </div>
  );
}

function EducationList({
  items,
  scheme,
}: {
  items: PreviewProfile["education"];
  scheme: Scheme;
}) {
  return (
    <div className="space-y-0.5">
      {items.map((edu, i) => (
        <div key={i} className="text-[11px]" style={{ color: scheme.body }}>
          {`${edu.degree} ${edu.field}`.trim()}, {edu.institution}, {edu.year}
          {edu.honors && <span className="italic">{` — ${edu.honors}`}</span>}
        </div>
      ))}
    </div>
  );
}
