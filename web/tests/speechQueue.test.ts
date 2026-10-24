// Voice commentary queue: one line at a time, goals/red cards pre-empt, stale lines dropped, de-dupe, bound.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MAX_QUEUE,
  PRIORITY,
  clear,
  emptyQueue,
  enqueue,
  finish,
  isStale,
  pickVoice,
  takeNext,
  type QueueState,
  type SpeechItem,
} from "../src/lib/speechQueue.ts";

const item = (id: string, priority: number, clockMs = 0, wallMs = 0, kind: SpeechItem["kind"] = "commentary"): SpeechItem => ({
  id, text: `line ${id}`, lang: "en", priority, kind, clockMs, wallMs,
});

const add = (s: QueueState, i: SpeechItem) => enqueue(s, i).state;

test("never talks over itself: the next line waits until the current one finishes", () => {
  let s = add(emptyQueue(), item("a", PRIORITY.normal));
  let r = takeNext(s, 0, 0);
  assert.equal(r.item?.id, "a");
  s = add(r.state, item("b", PRIORITY.normal));
  r = takeNext(s, 0, 0);
  assert.equal(r.item, null, "busy speaking a");
  r = takeNext(finish(r.state), 0, 0);
  assert.equal(r.item?.id, "b");
});

test("goals and red cards pre-empt a lower-priority line; equal priority does not", () => {
  let s = takeNext(add(emptyQueue(), item("chat", PRIORITY.low)), 0, 0).state;
  const goal = enqueue(s, item("goal", PRIORITY.high));
  assert.equal(goal.preempt, true);
  s = finish(goal.state); // the player was stopped
  const r = takeNext(s, 0, 0);
  assert.equal(r.item?.id, "goal");
  const again = enqueue(r.state, item("goal-insight", PRIORITY.high));
  assert.equal(again.preempt, false, "a goal never cuts off another goal line");
  assert.equal(enqueue(r.state, item("chance", PRIORITY.normal)).preempt, false);
});

test("higher priority is spoken first, then oldest first", () => {
  let s = emptyQueue();
  s = add(s, item("low", PRIORITY.low, 0, 1));
  s = add(s, item("n1", PRIORITY.normal, 0, 2));
  s = add(s, item("high", PRIORITY.high, 0, 3));
  s = add(s, item("n2", PRIORITY.normal, 0, 4));
  const order: string[] = [];
  for (;;) {
    const r = takeNext(s, 0, 0);
    if (!r.item) break;
    order.push(r.item.id);
    s = finish(r.state);
  }
  assert.deepEqual(order, ["high", "n1", "n2", "low"]);
});

test("stale lines (>15 s match time and >4 s wall time) are dropped", () => {
  assert.equal(isStale(item("x", 1, 0, 0), 15_001, 4_001), true);
  assert.equal(isStale(item("x", 1, 0, 0), 15_001, 1_000), false, "at 10x speed a short wait is fine");
  assert.equal(isStale(item("x", 1, 0, 0), 10_000, 60_000), false, "paused match: not stale in match time");
  let s = add(emptyQueue(), item("old", PRIORITY.high, 0, 0));
  s = add(s, item("fresh", PRIORITY.low, 20_000, 9_000));
  const r = takeNext(s, 21_000, 10_000);
  assert.equal(r.item?.id, "fresh");
  assert.equal(r.state.queue.length, 0);
});

test("duplicates are ignored, empty text is ignored, queue is bounded", () => {
  let s = add(emptyQueue(), item("a", PRIORITY.normal));
  s = add(s, item("a", PRIORITY.normal));
  s = add(s, { ...item("blank", PRIORITY.high), text: "   " });
  assert.equal(s.queue.length, 1);
  for (let i = 0; i < MAX_QUEUE + 3; i++) s = add(s, item(`n${i}`, PRIORITY.normal, 0, 10 + i));
  s = add(s, item("goal", PRIORITY.high, 0, 100));
  assert.equal(s.queue.length, MAX_QUEUE);
  assert.equal(s.queue[0].id, "goal");
  assert.ok(!s.queue.some((q) => q.id === "a"), "oldest normal-priority line dropped first");
});

test("stop clears the queue and nothing replays", () => {
  let s = add(emptyQueue(), item("a", PRIORITY.normal));
  s = takeNext(add(s, item("b", PRIORITY.normal)), 0, 0).state;
  s = clear(s);
  assert.equal(s.current, null);
  assert.equal(s.queue.length, 0);
  assert.equal(add(s, item("b", PRIORITY.normal)).queue.length, 0);
});

test("pickVoice prefers the exact locale, then the language, then natural voices", () => {
  const voices = [
    { lang: "en-US", name: "Samantha", localService: true },
    { lang: "en-GB", name: "Daniel", localService: true },
    { lang: "es-MX", name: "Paulina", localService: true },
    { lang: "es-ES", name: "Microsoft Alvaro Online (Natural)", localService: false },
    { lang: "pt_BR", name: "Luciana", localService: true },
  ];
  assert.equal(pickVoice(voices, "en")?.name, "Daniel");
  assert.equal(pickVoice(voices, "es")?.name, "Microsoft Alvaro Online (Natural)");
  assert.equal(pickVoice(voices, "pt")?.name, "Luciana");
  assert.equal(pickVoice(voices, "de"), null);
});
