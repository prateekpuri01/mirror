"use client";

import { createContext, useCallback, useContext, useState } from "react";

interface JobSelectionState {
  selectedIds: Set<string>;
  isSelected: (id: string) => boolean;
  toggleOne: (id: string) => void;
  selectMany: (ids: string[]) => void;
  setSelection: (ids: Set<string>) => void;
  clearSelection: () => void;
  count: number;
}

const JobSelectionContext = createContext<JobSelectionState>({
  selectedIds: new Set(),
  isSelected: () => false,
  toggleOne: () => {},
  selectMany: () => {},
  setSelection: () => {},
  clearSelection: () => {},
  count: 0,
});

export function useJobSelection() {
  return useContext(JobSelectionContext);
}

export { JobSelectionContext };

export function useJobSelectionProvider(): JobSelectionState {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const isSelected = useCallback(
    (id: string) => selectedIds.has(id),
    [selectedIds],
  );

  const toggleOne = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectMany = useCallback((ids: string[]) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.add(id);
      return next;
    });
  }, []);

  const setSelection = useCallback((ids: Set<string>) => {
    setSelectedIds(new Set(ids));
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  return {
    selectedIds,
    isSelected,
    toggleOne,
    selectMany,
    setSelection,
    clearSelection,
    count: selectedIds.size,
  };
}
