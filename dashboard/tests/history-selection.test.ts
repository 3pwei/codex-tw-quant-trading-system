import assert from "node:assert/strict";
import test from "node:test";
import {
  allVisibleRunsSelected,
  toggleRunSelection,
  toggleVisibleRunSelection,
} from "../app/history/history-selection.ts";

test("individual history selection toggles without duplicating ids", () => {
  assert.deepEqual(toggleRunSelection([], "run-1"), ["run-1"]);
  assert.deepEqual(toggleRunSelection(["run-1"], "run-1"), []);
});

test("select all adds every visible run and preserves hidden selections", () => {
  assert.deepEqual(
    toggleVisibleRunSelection(["hidden"], ["run-1", "run-2"]),
    ["hidden", "run-1", "run-2"],
  );
});

test("select all clears only visible runs when they are already selected", () => {
  const selected = ["hidden", "run-1", "run-2"];
  assert.equal(allVisibleRunsSelected(selected, ["run-1", "run-2"]), true);
  assert.deepEqual(
    toggleVisibleRunSelection(selected, ["run-1", "run-2"]),
    ["hidden"],
  );
});
