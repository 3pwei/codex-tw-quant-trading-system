import assert from "node:assert/strict";
import test from "node:test";
import {
  batchDeletionPayload,
  toggleRunSelection,
} from "../app/history/history-selection.ts";

test("individual history selection toggles without duplicating ids", () => {
  assert.deepEqual(toggleRunSelection([], "run-1"), ["run-1"]);
  assert.deepEqual(toggleRunSelection(["run-1"], "run-1"), []);
});

test("batch deletion uses explicit ids for an ordinary selection", () => {
  assert.deepEqual(
    batchDeletionPayload(false, ["run-1", "run-2"]),
    { run_ids: ["run-1", "run-2"] },
  );
});

test("batch deletion covers unloaded pages when all runs are selected", () => {
  assert.deepEqual(
    batchDeletionPayload(true, ["run-1"]),
    { delete_all: true },
  );
});
