"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { CompanyResearch } from "@/lib/types";
import { fetchCompanyResearch, runCompanyResearch } from "@/lib/api";

interface CompanyResearchPanelProps {
  jobId: string;
  /** Research snapshot from the resume document (if available) */
  documentResearch?: CompanyResearch | null;
}

export function CompanyResearchPanel({
  jobId,
  documentResearch,
}: CompanyResearchPanelProps) {
  const [research, setResearch] = useState<CompanyResearch | null>(
    documentResearch || null
  );
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);

  // Load from API if not provided via document
  useEffect(() => {
    if (documentResearch) {
      setResearch(documentResearch);
      return;
    }
    fetchCompanyResearch(jobId).then((r) => {
      if (r.available && r.research) setResearch(r.research);
    }).catch(() => {});
  }, [jobId, documentResearch]);

  const handleRefresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await runCompanyResearch(jobId, true);
      setResearch(result.research);
      setExpanded(true);
    } catch (err) {
      // Research refresh failed — user sees stale data
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  if (!research && !loading) return null;

  const companyLabel = research?.query_company || "Company";
  const teamLabel = research?.query_team;
  const headerLabel = teamLabel
    ? `${companyLabel}, ${teamLabel}`
    : companyLabel;

  return (
    <div className="border rounded-md bg-slate-50 text-xs mb-3">
      {/* Header */}
      <button
        className="flex items-center justify-between w-full px-3 py-2 hover:bg-slate-100 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex items-center gap-1.5 font-medium text-slate-700">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          <span>Company Research</span>
          <span className="text-slate-400 font-normal">
            {" "}
            &mdash; {headerLabel}
          </span>
        </div>
        <button
          className="flex items-center gap-1 text-[11px] text-slate-500 hover:text-amber-600 px-1.5 py-0.5 rounded hover:bg-white transition-colors"
          onClick={(e) => {
            e.stopPropagation();
            handleRefresh();
          }}
          disabled={loading}
        >
          <RefreshCw
            className={`h-3 w-3 ${loading ? "animate-spin" : ""}`}
          />
          {loading ? "Researching..." : "Refresh"}
        </button>
      </button>

      {/* Body */}
      {expanded && research && (
        <div className="px-3 pb-3 space-y-2.5 border-t">
          {/* Summary */}
          <div className="pt-2">
            <p className="text-slate-700">
              {research.company_summary}{" "}
              <span className="text-slate-400">{research.company_stage}</span>
            </p>
          </div>

          {/* Team function */}
          {research.team_function &&
            research.team_function !== "unknown" &&
            research.team_function !== "General technical role" && (
              <div>
                <span className="font-semibold text-slate-600">
                  Team Focus:{" "}
                </span>
                <span className="text-slate-700">
                  {research.team_function}
                </span>
              </div>
            )}

          {/* Recent work */}
          {research.team_recent_work?.length > 0 && (
            <div>
              <div className="font-semibold text-slate-600 mb-0.5">
                Recent Work
              </div>
              <ul className="list-disc list-inside text-slate-600 space-y-0.5">
                {research.team_recent_work.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Open problems */}
          {research.team_open_problems?.length > 0 && (
            <div>
              <div className="font-semibold text-slate-600 mb-0.5">
                Problems They&apos;re Solving
              </div>
              <ul className="list-disc list-inside text-slate-600 space-y-0.5">
                {research.team_open_problems.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Framing angles — the most important section */}
          {research.framing_angles?.length > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded px-2.5 py-2">
              <div className="font-semibold text-amber-800 mb-1">
                Resume Framing Guidance
              </div>
              <ol className="list-decimal list-inside text-amber-900 space-y-0.5">
                {research.framing_angles.map((angle, i) => (
                  <li key={i}>{angle}</li>
                ))}
              </ol>
            </div>
          )}

          {/* Valued skills */}
          {research.valued_skills?.length > 0 && (
            <div>
              <span className="font-semibold text-slate-600">
                Skills They Value:{" "}
              </span>
              <span className="text-slate-600">
                {research.valued_skills.join(", ")}
              </span>
            </div>
          )}

          {/* Tech signals */}
          {research.tech_signals?.length > 0 && (
            <div>
              <span className="font-semibold text-slate-600">
                Tech Signals:{" "}
              </span>
              <span className="text-slate-600">
                {research.tech_signals.join(", ")}
              </span>
            </div>
          )}

          {/* Citations */}
          {research.citations?.length > 0 && (
            <div className="text-[10px] text-slate-400 pt-1 border-t border-slate-200">
              Sources: {research.citations.slice(0, 5).map((url, i) => (
                <span key={i}>
                  {i > 0 && " | "}
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline hover:text-slate-600"
                  >
                    {new URL(url).hostname}
                  </a>
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
