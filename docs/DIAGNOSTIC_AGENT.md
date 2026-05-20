# Diagnostic Agent

The dashboard diagnostic agent is a separate operator layer above the Pokemon
autoplayer. It helps explain blockers, propose safe recovery steps, and verify
whether the bot improved. It does not replace V1, V2, adaptive, or unified
gameplay policy.

## Loop Contract

The agent follows a bounded loop:

```text
observe -> hypothesize -> propose -> approve -> execute -> verify -> learn/stop
```

The dashboard enforces the important parts of this loop:

- Read-only diagnosis can run immediately.
- Runtime changes become typed proposals in the approval card.
- Approved live troubleshooting saves a restore point first, resumes the bot,
  samples live status, pauses the bot again, and offers a reload card.
- The agent loop runs at most three observe/self-critique passes before stopping.
- One actionable proposal is allowed at a time; a new user message supersedes the
  previous card.

## Agent Loop Mode

Use the `agent loop` button or ask for phrases such as:

- `run a diagnostic loop`
- `iterate to resolve blockers`
- `make it go brrrr`

The loop gathers compact live context from Hermes diagnostics, asks the
diagnostic agent to critique its previous hypothesis, and stops when one of these
conditions is met:

- an approval card can be created for a safe next step;
- the agent reports a verified no-op or no action needed;
- the three-iteration budget is exhausted;
- context or chat streaming fails.

The loop is intentionally not a free-running controller. It can reason across
iterations, but it cannot mutate the emulator or autoplayer without the existing
approval flow.

## Live Troubleshooting Mode

Use `live troubleshoot` when the issue depends on short-term movement, battle, or
verification behavior. The approved probe is reversible:

```text
save state -> enable bot -> observe snapshots -> pause bot -> summarize -> offer reload
```

The reload approval card uses `/load` and then keeps the autoplayer paused.

## Proposal Classes

| Class | Examples | Approval |
| --- | --- | --- |
| Read-only | summarize blockers, explain policy, inspect status deltas | not required |
| Diagnostic loop | up to three observe/self-critique passes | required to start |
| Reversible probe | save/resume/observe/pause live troubleshooting | required |
| Mutation | control changes, manual actions, new runs, reload state | required |

The dashboard currently allows these mutation types:

- `autoplayer_control`: `POST /autoplayer/control`
- `manual_action`: `POST /action`
- `new_run`: `POST /runs/new`
- `load_state`: `POST /load`
- `live_troubleshoot`: reversible live probe
- `diagnostic_loop`: bounded self-critique loop
- `operator_approval`: records that the human approved an out-of-band fix plan

## Evidence Sources

The diagnostic agent should prefer structured status over raw screenshots:

- `/state`: player position, battle state, party, bag, money, map information.
- `/autoplayer/status`: engine control, readiness blockers, current intent,
  decision trace, policy candidates, navigation verification, recent failures,
  supervisor health, and learning summaries.
- `/watch/status`: viewer/share state and stream health.
- Compact diagnostic session memory from prior chat turns.

High-value blocker fields include:

- `control.enabled`, `dry_run`, `allow_overworld_movement`,
  `allow_battle_actions`
- `v2_readiness.blockers`
- `status.readiness.blockers`
- `status.resource_accounting.readiness_blockers`
- `status.navigation.blocked_reason`
- `status.navigation.last_step_verified`
- `status.navigation.verification_reason`
- `supervisor_health.stale`

## Safety Rules

- Do not auto-approve agent-generated proposals.
- Do not execute more than one logical mutation without fresh observation.
- Do not repeatedly propose the same recovery unless state changed.
- Do not use save-state search, future/RNG peeking, hidden emulator state, or ROM
  data to choose gameplay actions.
- Pause after live troubleshooting unless the user explicitly approves continued
  play.
- Prefer reversible probes and typed control changes over long input macros.

## Making The Bot Improve

The diagnostic agent improves the Pokemon agent by tightening the outer loop:

- It detects whether a blocker is policy, readiness, control, supervisor, or UI.
- It proposes the smallest safe change instead of broad manual intervention.
- It verifies with status deltas instead of relying on chat confidence.
- It preserves operator context through compact session memory.
- It leaves durable gameplay learning to the autoplayer memory files, while
  keeping diagnostic reasoning in the dashboard transcript.

Future server-side work can persist diagnostic loop records as JSONL under the
profile data directory, but the browser implementation already enforces the key
safety property: reasoning may iterate, mutations still require approval.
