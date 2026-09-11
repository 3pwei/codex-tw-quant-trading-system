export function toggleRunSelection(selected: string[], runId: string): string[] {
  return selected.includes(runId)
    ? selected.filter(item => item !== runId)
    : [...selected, runId];
}

export function allVisibleRunsSelected(
  selected: string[],
  visible: string[],
): boolean {
  return visible.length > 0 && visible.every(runId => selected.includes(runId));
}

export function toggleVisibleRunSelection(
  selected: string[],
  visible: string[],
): string[] {
  const next = new Set(selected);
  if (allVisibleRunsSelected(selected, visible)) {
    visible.forEach(runId => next.delete(runId));
  } else {
    visible.forEach(runId => next.add(runId));
  }
  return [...next];
}
