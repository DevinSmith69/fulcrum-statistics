import assert from "node:assert/strict";
import { test } from "node:test";
import { askCopy, askErrorMessage, cleanQuestion, MAX_QUESTION, suggestedQuestions } from "../src/lib/ask.ts";

test("three suggestions per language, English for unknown languages", () => {
  for (const lang of ["en", "es", "fr", "pt", "de"]) assert.equal(suggestedQuestions(lang).length, 3);
  assert.deepEqual(suggestedQuestions("pt-BR"), suggestedQuestions("pt"));
  assert.deepEqual(suggestedQuestions("xx"), suggestedQuestions("en"));
  assert.deepEqual(suggestedQuestions(null), suggestedQuestions("en"));
});

test("questions are trimmed and bounded", () => {
  assert.equal(cleanQuestion("  Who's   on top?\n"), "Who's on top?");
  assert.equal(cleanQuestion("   "), null);
  assert.equal(cleanQuestion("x".repeat(MAX_QUESTION + 1)), null);
  assert.equal(cleanQuestion("x".repeat(MAX_QUESTION))?.length, MAX_QUESTION);
});

test("errors become viewer-friendly copy in the viewer's language", () => {
  assert.equal(askErrorMessage("503 the agents service is not running", "en"), askCopy("en", "unavailable"));
  assert.equal(askErrorMessage("429 too many questions", "es"), askCopy("es", "busy"));
  assert.equal(askErrorMessage("Failed to fetch", "de"), askCopy("de", "failed"));
  assert.equal(askCopy("fr", "title"), "Demandez au studio");
  assert.equal(askCopy("fr", "nope"), "nope");
});
