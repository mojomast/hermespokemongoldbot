# React Bot Dashboard Spec

## Purpose

Build a new interactive React dashboard for the Pokemon autoplayer that makes the
bot easy to understand, debug, and improve. The dashboard should explain what the
bot is doing, why it chose each action, what goal it is pursuing, what evidence it
trusts, when it is stuck, and why it switches modes.

The current static dashboard exposes useful raw telemetry. The new dashboard
should turn that telemetry into a clear mission-control experience for bot
development and live observation.

## Product Goal

The user should always be able to answer:

- What is the bot doing right now?
- Why did it choose this action?
- What is the current goal and subgoal?
- What does the bot expect to happen next?
- Did the last action verify successfully?
- What did it learn?
- Is it stuck or recovering?
- Did it switch modes, and why?
- Which game/profile capabilities are active?

## Design Metaphor

Use the bot-as-field-researcher metaphor:

- The emulator is the bot's sensory feed.
- The current route/objective is the mission plan.
- Confidence and readiness are the signal meters.
- Learning memory is the lab notebook.
- The event timeline is the flight recorder.
- Mode handoff is an escalation ladder.
- Unified/adaptive policy selection is a policy council.

The dashboard must connect raw actions to intent and evidence. Do not show only
`press_a` or `walk_left`; show why the action was selected, what was expected,
how it was verified, and what learning changed.

## Recommended Stack

- React 19
- TypeScript
- Vite
- React Router v7
- TanStack Query for REST snapshots
- Zustand for local/live UI state
- ECharts for rich dashboard charts
- uPlot for dense high-frequency time-series if needed
- TanStack Table and TanStack Virtual for logs/tables
- Radix UI or React Aria for accessible primitives
- CSS variables plus Tailwind or CSS Modules for styling
- Zod or Valibot for runtime API validation
- Vitest and Testing Library for component/unit tests
- MSW for API mocks
- Playwright for E2E and accessibility smoke tests

Avoid:

- Create React App
- Enzyme
- Redux as the default store for every state type
- Giant global Context objects for live data
- Rendering every WebSocket message directly through React state
- Unbounded arrays of telemetry in component state
- Snapshot-heavy tests
- Heavy SVG charts for dense realtime streams

## App Location

Create the React app under:

```text
pokemon_agent/dashboard/react/
```

Build output should be served by the existing FastAPI static dashboard route.
Keep these pages separate for now:

- `/dashboard/watch.html`: read-only public viewer.
- `/dashboard/onboarding.html`: ROM upload/onboarding.

The first migration target is the full local control dashboard at `/dashboard/`.
The old static dashboard should remain available during migration as a fallback,
for example under `/dashboard/legacy.html`.

## Suggested Source Layout

```text
src/
  app/
    router.tsx
    providers.tsx
    queryClient.ts
  features/
    dashboard/
      DashboardPage.tsx
      components/
      data/
      realtime/
      charts/
    bot-intent/
    live-state/
    learning/
    timeline/
    handoff/
    controls/
  shared/
    api/
    ui/
    hooks/
    lib/
    types/
```

Use feature-oriented modules. Keep API transport, normalization, and UI rendering
separate.

## Data Architecture

Use REST snapshots plus live event streams:

```text
HTTP snapshot -> TanStack Query cache
WebSocket events -> validate -> normalize -> bounded live store
UI selectors -> panels/charts/timeline
```

Rules:

- Fetch initial snapshots before applying WebSocket deltas.
- Validate API payloads at boundaries.
- Keep high-frequency ring buffers outside React component state.
- Batch live updates with `requestAnimationFrame` or a 250ms interval.
- Cap in-memory telemetry windows.
- Show stale/degraded/reconnecting states clearly.
- Use `startTransition` for non-urgent UI updates.
- Use `useDeferredValue` for expensive filtering/search.
- Use `useEffectEvent` for WebSocket callbacks that need latest state without
  reconnecting.

Primary endpoints:

- `GET /state`
- `GET /autoplayer/status`
- `POST /autoplayer/control`
- `POST /action`
- `GET /rtc/debug/perf`
- `GET /watch/status`
- `GET /roms`
- `GET /runs`
- `GET /saves`
- `WS /ws`
- `WS /watch/ws`

Important proxy note: Hermes currently proxies Pokemon HTTP routes under
`/pokemon`, but WebSocket proxying may require additional work. The React app
must centralize API and WebSocket base URL handling instead of relying on hardcoded
root paths.

## Main Layout

Use a three-column mission-control layout with a bottom timeline:

```text
┌──────────────────────┬────────────────────────────┬──────────────────────┐
│ Live Game State      │ Bot Intent + Decision Flow  │ Evidence + Learning  │
│ Emulator / Map / RAM │ Goal, why, next action      │ Memory, stuck, stats │
├──────────────────────┴────────────────────────────┴──────────────────────┤
│ Timeline / Event Trace / Handoff / Mode Diagnostics                       │
└───────────────────────────────────────────────────────────────────────────┘
```

The center column is the truth source for intent. The left column shows what the
bot sees. The right column shows what the bot knows and learns.

## Required Panels

### 1. Mode Banner

Persistent top banner showing:

- Game name and profile.
- Selected engine.
- Supervisor active engine.
- Current phase.
- Live/stale/reconnecting state.
- Safety gates.
- Last handoff reason.

Mode chips:

- `Gold/Silver`
- `V1`
- `V2`
- `Adaptive`
- `Unified`
- `Red/Blue`
- `Yellow`
- `Generic GB`
- `Map-Aware`
- `Learning Active`
- `Dry Run`
- `Live Actions`

Clicking the banner opens a Mode Inspector with capability matrix and active
policy source.

### 2. Live Game State

Shows what the bot currently observes:

- WebRTC emulator viewport.
- Screenshot fallback if WebRTC unavailable.
- Map name, map group/number, map id.
- Position and raw position.
- Facing direction.
- Party summary.
- Inventory summary.
- Battle state.
- Menu/dialog state.
- Visual classifier state.
- RAM/read health.

Optional overlays:

- Player tile.
- Facing arrow.
- Target tile.
- Planned route.
- Blocked edges.
- Warp/transition tiles.
- Untrusted/unknown tiles.

### 3. Bot Intent Card

The most important panel. It must show:

- Current high-level goal.
- Current subgoal.
- Next logical action.
- Posted low-level actions.
- Reason for action.
- Expected outcome.
- Last verification result.
- Confidence.
- Alternatives considered.
- Abort condition.
- Recovery condition.

Example:

```text
Goal: Return Mystery Egg to Elm
Subgoal: Exit Cherrygrove east
Action: walk_right
Expected: transition to Route 29
Reason: shortest static route to Elm's Lab
Verification: map_transition_observed
Confidence: high
```

### 4. Decision River

Clickable pipeline:

```text
Observe -> Interpret -> Choose Goal -> Plan -> Act -> Verify -> Learn
```

Each stage expands to show:

- Inputs.
- Derived features.
- Policy outputs.
- Confidence.
- Errors/fallbacks.
- Linked timeline events.

### 5. Goal Stack And Motivation

Show prioritized goals and motivations:

- Survive.
- Heal.
- Progress story.
- Win battle.
- Catch Pokemon.
- Train team.
- Explore.
- Recover from stuck state.

Each goal should display:

- Priority.
- Activation reason.
- Progress.
- Blocking conditions.
- Evidence IDs.
- Expiration condition.

Motivation meters:

```text
Survival:       High
Progression:    Medium
Exploration:    Low
Training:       Low
Resource Gain:  Low
Recovery:       Medium
```

### 6. Stuck And Handoff Panel

Use a traffic-light/escalation ladder:

- Green: normal.
- Yellow: uncertain.
- Orange: recovery active.
- Red: handoff requested/performed.
- Gray: paused/manual/dry-run.

Show:

- Current stuck score.
- Recovery level.
- Recent failed actions.
- Blocked edges.
- Button failures.
- Handoff cooldown.
- Last handoff from/to/reason.
- Suggested developer actions.

Controls:

- Take Control.
- Resume Bot.
- Force Replan.
- Switch Mode.
- Toggle Auto Handoff.
- Create Debug Snapshot.

### 7. Policy Council

For unified/adaptive debugging, show candidate policies:

```text
V1 teacher:      press_a, confidence 54%, reason: legacy battle recovery
V2 planner:      no action, confidence 20%, reason: missing menu state
Adaptive policy: press_a, confidence 80%, reason: trainer missing-menu fallback

Selected: press_a
Why: adaptive recovery has verified battle progress in this context
```

This panel may initially be partially populated from current status fields. Add a
backend `policy_candidates` envelope later.

### 8. Learning Notebook

Show categorized memory facts and recent learning updates:

- `PKM:OBJECTIVE`
- `PKM:PROGRESS`
- `PKM:MAP`
- `PKM:STUCK`
- `PKM:TEAM`
- `PKM:STRATEGY`

Learning cards should show:

- Observation.
- Before/after state.
- Confidence before/after.
- Source.
- Game id.
- Linked action/event.
- Evidence count.
- Whether it came from V1 teacher, V2, adaptive, unified, or developer label.

### 9. Flight Recorder Timeline

Virtualized bottom event stream showing:

- Observation.
- Goal change.
- Motivation change.
- Plan generated.
- Action emitted.
- Verification result.
- Learning update.
- Stuck warning.
- Recovery attempt.
- Handoff.
- Mode switch.
- Developer override.
- Battle event.
- Menu event.

Interactions:

- Filter by type.
- Search by map/action/goal.
- Click event for full JSON/details.
- Compare two events.
- Pin event.
- Export debug bundle.
- Pause live updates.
- Jump to live.

## Cross-Game Requirements

### Gold/Silver

Gold/Silver should expose full planner and learning details:

- V1/V2/adaptive/unified mode comparison.
- Static route path.
- Story objective.
- Blocked-edge map.
- V1 teacher facts.
- V2 verified transitions.
- Adaptive recovery state.
- Auto handoff.

### Red/Blue

Run through unified/generic mode until Kanto map/story plugins exist.

Dashboard must show:

- `red_blue` profile.
- Capabilities available/unavailable.
- Generic exploration policy.
- State uncertainty.
- Learned facts scoped to `game_id: "red_blue"`.

Do not display Gold route/story planner panels as active for Red/Blue.

### Yellow

Yellow must be separate from Red/Blue until RAM offsets and behavior are
validated.

Dashboard must show:

- `yellow` profile.
- Unvalidated/partial RAM support.
- Conservative fallback policy.
- Learned facts scoped to `game_id: "yellow"`.

### Generic GB/GBC

For unknown compatible Pokemon games:

- Show generic profile.
- Emphasize uncertainty.
- Offer developer labeling prompts.
- Avoid game-specific planner claims.

## Normalized UI Types

Create frontend adapters that normalize existing server payloads into these UI
models.

### Runtime State

```ts
type RuntimeState = {
  game: "gold" | "silver" | "red" | "blue" | "yellow" | "unknown";
  profile: "gold_silver" | "red_blue" | "yellow" | "generic_gb";
  selectedEngine: "v1" | "v2" | "adaptive" | "unified";
  activeEngine?: "v1" | "v2" | "adaptive" | "unified";
  phase: "overworld" | "battle" | "menu" | "dialogue" | "transition" | "recovery" | "unknown";
  frame?: number;
  updatedAt?: number;
  stale: boolean;
};
```

### Bot Intent

```ts
type BotIntent = {
  goal: string;
  subgoal?: string;
  nextAction?: string;
  postedActions: string[];
  reason?: string;
  expectedOutcome?: string;
  verification?: string;
  confidence: {
    overall?: number;
    state?: number;
    goal?: number;
    plan?: number;
    action?: number;
    learning?: number;
  };
  alternatives: CandidateAction[];
  abortCondition?: string;
  recoveryCondition?: string;
};
```

### Candidate Action

```ts
type CandidateAction = {
  policy: "v1" | "v2" | "adaptive" | "unified" | "teacher" | "fallback";
  action?: string;
  confidence?: number;
  reason: string;
  selected: boolean;
};
```

### Timeline Event

```ts
type TimelineEvent = {
  id: string;
  timestamp: number;
  frame?: number;
  type:
    | "observe"
    | "goal"
    | "motivation"
    | "plan"
    | "action"
    | "verify"
    | "learn"
    | "warning"
    | "recovery"
    | "handoff"
    | "mode_switch"
    | "developer_override"
    | "battle"
    | "menu";
  summary: string;
  details: unknown;
  relatedIds: string[];
};
```

### Learning Evidence

```ts
type LearningEvidence = {
  id: string;
  category:
    | "PKM:OBJECTIVE"
    | "PKM:PROGRESS"
    | "PKM:MAP"
    | "PKM:STUCK"
    | "PKM:TEAM"
    | "PKM:STRATEGY"
    | "PKM:POLICY"
    | "PKM:RESOURCE"
    | "PKM:FAILURE"
    | "PKM:OUTCOME";
  gameId: string;
  source: "v1_teacher" | "v1" | "v2" | "v2_adaptive" | "unified" | "developer";
  text: string;
  confidence: string;
  evidenceCount?: number;
  firstSeenAt?: number;
  lastSeenAt?: number;
  linkedEventIds: string[];
};
```

## Backend Enhancements Needed

The current vanilla dashboard already consumes the first backend explainability
envelope from `/autoplayer/status`. A future React version should preserve and
expand this contract:

```json
{
  "intent": {
    "goal": "Return Mystery Egg to Elm",
    "subgoal": "Exit Cherrygrove east",
    "next_action": "walk_right",
    "reason": "shortest static route to Elm's Lab",
    "expected_outcome": "map transition to Route 29",
    "confidence": {}
  },
  "decision_trace": {
    "observe": {},
    "interpret": {},
    "choose_goal": {},
    "plan": {},
    "act": {},
    "verify": {},
    "learn": {}
  },
  "policy_candidates": [],
  "mode_switch": {},
  "resource_accounting": {},
  "failure_memory": {},
  "shared_learning": {},
  "evidence": []
}
```

The control dashboard renders these fields in the AI Decision Inspector directly
below the video output, showing mode handoffs and why, current intent, selected
policy, readiness/resource blockers, policy candidates, recent learning facts, and
the observe -> assess -> choose -> act -> learn trace.

Add later:

- Stable event IDs.
- Monotonic sequence numbers for WebSocket events.
- Cursor-paginated timeline endpoint.
- Normalized policy candidate data.
- Explicit confidence fields.
- Event-linked learning facts.
- Debug snapshot/export endpoint.

## Accessibility Requirements

- Full keyboard access for all controls.
- Visible focus states.
- Proper landmarks and headings.
- Do not use color as the only signal.
- Respect `prefers-reduced-motion`.
- Provide textual summaries for charts/canvas visualizations.
- Use `aria-live` only for connection/critical status, not high-frequency telemetry.
- Make live updates pausable.
- Use accessible primitives for dialogs, menus, tabs, and popovers.

## Performance Requirements

- Do not keep raw screenshots/base64 frames in React state.
- Do not store unbounded event arrays.
- Virtualize logs and tables.
- Use canvas/WebGL-backed charts for dense streams.
- Downsample long time ranges.
- Batch stream updates.
- Code split heavy chart/map panels.
- Keep WebRTC video outside normal React re-render loops.
- Track stale data, message lag, reconnects, long tasks, and dropped frames.

## Testing Requirements

Unit/component tests:

- API payload adapters.
- Mode/profile normalization.
- Intent card rendering.
- Handoff panel states.
- Timeline filtering.
- Safety gate controls.

Integration tests:

- Mock `/state` and `/autoplayer/status` with MSW.
- Simulate V1/V2/adaptive/unified status payloads.
- Simulate Red/Blue/Yellow generic profiles.
- Simulate stale supervisor and handoff events.

E2E tests:

- Dashboard loads and shows live/stale state.
- Engine can be changed safely.
- Dry-run and live-action gates are preserved.
- Handoff warning appears.
- Battle missing-menu recovery is explainable.
- WebRTC/screenshot fallback works.

## Rollout Plan

### Phase 1: Foundation

- Add Vite React app under `pokemon_agent/dashboard/react/`.
- Add typed API client and payload adapters.
- Render the React dashboard at a new path such as `/dashboard/react.html`.
- Keep old dashboard unchanged.

### Phase 2: Core Explainability

- Implement Mode Banner.
- Implement Live Game State panel.
- Implement Bot Intent Card.
- Implement Stuck/Handoff panel.
- Implement Timeline panel using current recent logs.

### Phase 3: Control Parity

- Port engine selector.
- Port objective/guidance fields.
- Port dry-run and live-action gates.
- Port auto-handoff controls.
- Port manual controls.
- Port saves/runs/ROM panel.

### Phase 4: Learning And Policy Council

- Add Learning Notebook.
- Add V1 teacher/V2/adaptive/unified source filters.
- Add Policy Council from current status where possible.
- Add richer backend `policy_candidates` later.

### Phase 5: Map And Replay

- Add route/map overlays.
- Add blocked-edge visualization.
- Add timeline event detail drawer.
- Add debug snapshot/export.

### Phase 6: Replace Legacy Dashboard

- Move React app to `/dashboard/`.
- Keep legacy page under `/dashboard/legacy.html`.
- Update Hermes Games tab links if needed.
- Add WebSocket proxy support in Hermes if embedded realtime is required.

## Success Criteria

- A developer can understand the bot's current action and reason in under five
  seconds.
- Stuck and handoff states are visually obvious.
- Gold/Silver mode differences are clear.
- Red/Blue/Yellow uncertainty is represented honestly.
- Live updates stay smooth without UI jank.
- Safety gates are impossible to miss.
- The dashboard can explain a replay of the rival battle missing-menu recovery.
- Tests cover mode rendering, safety controls, and handoff states.

## Non-Goals For First Version

- Do not rewrite watch/onboarding pages immediately.
- Do not add SSR unless there is a clear requirement.
- Do not implement a full design system before core panels work.
- Do not require backend schema rewrites before the first React dashboard.
- Do not make Red/Blue/Yellow appear more capable than they currently are.
