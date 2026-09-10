# The Dawg / APK Forge — v4.0

Two headline changes: the model is now **GLM-5.3-Flash** (DeepSeek dropped), and there's a
new front door — **the Station**, one plain-English command box that builds, updates and
fixes for you.

---

## 1. GLM-5.3-Flash, done right

Swapping the model isn't a one-line constant change, because **GLM-5.3-Flash cannot turn
thinking off**. Its chat template opens a `<think>` block in the *generation prompt*, so the
model always reasons, and that reasoning arrives mixed into the response. A code generator
that treats the raw response as the app would paste the chain-of-thought straight into your
`main.py`. Everything below is what makes GLM actually produce good apps here.

- **Default model is `zai-org/GLM-5.3-Flash`.** DeepSeek is gone from the constants,
  `DEFAULT_CONFIG`, the model dropdown and the key-test. Groq stays as the fallback.

- **Reasoning is stripped before parsing (`strip_think` / `content_of`).** Three shapes are
  handled: a closed `<think>…</think>` block, an **orphaned trailing `</think>`** (the opener
  was in the prompt, so the model only emits the closer), and an unclosed opener (truncated
  mid-thought → treated as no answer). It only strips reasoning that sits *before* the
  `<<<…>>>` section markers, so a `</think>` that ever appears inside real code can't truncate
  your app. Where the provider returns the CoT in a separate `reasoning_content` field, we
  just take the clean `content`. `build_forge_payload` strips again as defence in depth.

- **Reasoning effort is sent explicitly (`reasoning_params`).** GLM-5.3 *defaults to `max`
  reasoning* if the field is absent, so every call would silently run at max. We send
  `reasoning_effort` (Z.ai/vLLM dialect) **and** `enable_thinking` + `thinking_budget`
  (SiliconFlow's own dialect) — they agree in direction. Default effort is **low**: a code
  generator wants code, not a plan. Settable to low/high/max in Settings.

- **Strip-and-retry on HTTP 400 (`_chat_completion`).** If a strict backend rejects the extra
  reasoning keys, the call retries once without them instead of the whole round dying on an
  unrecognised field.

- **Reasoned-silent recovery.** If GLM spends its whole budget thinking and returns an empty
  answer, `call_ai` doesn't re-send the identical request in a loop — it re-asks once with
  minimal reasoning, a bigger token ceiling, and an explicit "answer now, don't think
  further", then takes that.

- **`max_tokens` default raised 12000 → 20000.** GLM always spends some output on reasoning
  before it writes the app; a tight ceiling truncated the app mid-file. The reasoning is
  stripped from what's kept, but it still has to fit while streaming.

- **Key-test fixed for thinking models.** It asked for 3 tokens — GLM would spend those on
  reasoning and come back empty, looking like a failure. Now 64 tokens + minimal reasoning.

## 2. The Station — one box that does everything

A full-width command bar at the top of the window. You type what you want; it figures out
what to do:

- **Empty editor →** forges a brand-new app from your instruction, then verifies it.
- **App open →** applies your instruction as an **edit** to that app (add a screen, a toggle,
  a sound, a stat; make things bigger; fix a bug), then verifies it.

Either way it runs the same **forge → repair → static gate → self-test → fix** loop to
completion, streaming into the existing agent rail, and drops the finished app in the editor.
Quick-start chips (theme toggle, settings screen, scores, sound, polish, find & fix bugs) and
Ctrl+Enter to run.

New under the hood: `EDIT_PROMPT` + `ai_edit()` (apply a plain-English change to the current
app, kit stripped both ways), `run_station()` (route new-vs-edit and drive the verify loop),
and the `/api/station` endpoint. The edit path guards against a no-op response ("the model
returned the app unchanged") instead of silently looking broken.

## 3. GUI overhaul — a live command station

The window is now three clearly labelled sections instead of two loose columns, with a
Cowork-style live activity stream as the centrepiece:

- **BUILD** (left) — the Station's classic controls: AI Forge / Manual tabs, describe box,
  quick chips. Everything that was there, kept.
- **THE APP** (centre) — the app itself: meta fields, the line-numbered editor, live lint,
  validation, Improve-with-AI, and the whole action bar (Build / Preview / Self-test /
  Auto-fix / Repair / Polish / Download). The section header shows the app title and a live
  syntax pill.
- **LIVE ACTIVITY** (right, always on) — **every** action now narrates itself here, live,
  with timestamps and status icons: forging, editing, static-analysis, each self-test phase,
  fixes, and builds — not just the verify loop as before. The build log tucks under it in a
  collapsible.

How it's wired: one `logAct()` feed engine; `toast()` and `busy()` pipe into it so every
completion and every action-start is captured with almost no per-function edits; `pollJob()`
now *appends* job steps into that single stream (interleaved chronologically) instead of
replacing a separate rail. Sticky section headers, a running status pill + pulse dot, and a
responsive stack that collapses to one column under 1180px (the desktop app runs it wide).
Keeps the amber "Dawg" identity and every existing element id/handler.

## 4. Review pass — bugs & inconsistencies fixed

- **Key-test could cry failure on a working key.** The `/api/keytest` probe was sending the
  GLM reasoning params, but — unlike `call_ai` — it has no 400 strip-and-retry, so a backend
  that rejected those keys would report the key/model broken while forging (which retries)
  worked fine. It's an auth/connectivity probe: success is HTTP 200. Reasoning params dropped;
  `max_tokens` stays generous (64) so an always-thinking model still returns 200.
- **`thinking_budget` could exceed the whole output ceiling.** At `high`/`max` the budget
  (8192 / 24576) was larger than the answer had room for under a 20000 `max_tokens` cap —
  nonsense the backend can't honour. Rebalanced to 2048 / 6144 / 12288, always well under the
  default ceiling so the app code always has room.
- **Silent-retry no longer stacks two user turns.** The reasoned-silent recovery appended a
  second `user` message; some chat templates expect strict role alternation. The "answer now"
  nudge is now folded into the last user turn (on a copy — the caller's messages are left
  untouched).
- **Activity feed polish:** a `clear` control in the LIVE ACTIVITY header; the section header
  now leads the run spinner (was appearing above it); feed capped at 260 rows; XSS-safe rows
  (textContent, not innerHTML).

## Verified

- `selftest.py` **336/336** and `selftest_modules.py` **51/51** green, no test edits.
- Review-pass fixes unit-tested: reasoning budgets all sit under `max_tokens`; the key-test
  payload carries no reasoning params; the silent-retry merges the nudge into the last user
  turn without adding a message or mutating the caller's list, and still recovers the app.
- New GLM logic unit-tested: `strip_think` across all four shapes incl. the in-code
  `</think>` false-positive guard; `content_of`; `reasoning_params` per family; payload
  parsing through a think block.
- Request path proven with a mocked backend: reasoning params land in the body; a 400 on
  those params triggers a stripped retry; the reasoned-silent case retries with an answer-now
  nudge and recovers.
- Live server: `/api/config` round-trips `reasoning_effort`; `/api/station` forges a new app
  and edits an existing one end-to-end.
- GUI: JS parses (node --check), braces/parens balanced, every new id and handler resolves.
- **Not testable in this sandbox:** a real buildozer APK build, and live SiliconFlow/Groq
  calls (no key). Confirm the model id `zai-org/GLM-5.3-Flash` against your SiliconFlow
  account on first run — if it 404s, the exact string is editable in Settings → Model.
