export function toggleRunSelection(selected: string[], runId: string): string[] {
  return selected.includes(runId)
    ? selected.filter(item => item !== runId)
    : [...selected, runId];
}

export function batchDeletionPayload(
  allRunsSelected: boolean,
  selectedRunIds: string[],
): { delete_all: true } | { run_ids: string[] } {
  return allRunsSelected
    ? { delete_all: true }
    : { run_ids: selectedRunIds };
}
