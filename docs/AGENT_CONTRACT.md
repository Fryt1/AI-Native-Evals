# The Agent contract

Today "an Agent" is not one thing in this repository. It is five things, in five
places, connected by convention rather than by a declared relationship:

| What | Where | Who knows it |
| --- | --- | --- |
| How to build its image | `tools/build-sandbox-images.ps1` | a PowerShell switch per Agent |
| Where its image lives | `profiles/agents/<id>.yaml` `image` | the profile |
| How to start it | the same profile, `entrypoint` / `command` | the profile |
| How to talk to it | the same profile, `adapter` + `protocol` | the profile, and `adapters/` |
| How to read its log | the same profile, `adapter` | `adapters/events.py` |

Adding an Agent meant editing a build script that enumerated Agents by name,
adding a profile, and — when the protocol is new — writing an adapter. The
enumeration is what made this not an abstraction: `-IncludeDshRelease`,
`-CodexVersion` and `-DshVersion` were switches for two specific Agents, and a
third Agent needed a third switch.

## What this document proposes

An Agent becomes one declared object with named parts. A profile states all of
them; anything that needs to know something about an Agent reads that
declaration instead of knowing it itself.

```
Agent
├── identity    id, label, version (or commit)
├── build       how its image is produced
├── launch      image, entrypoint, command, environment, workdir, writable paths
├── protocol    how a conversation is carried (responses, acp, ...)
└── trace       how its log becomes normalized events
```

Every part is already present somewhere. The change is that they are stated in
one place, and that the parts nobody declared yet — `build`, and an explicit
`trace` — gain a home.

## The parts

### identity

```yaml
id: codex
label: Codex
agent_version: 0.153.4        # or a commit, for a source build
```

`agent_version` derives the image tag. A source build would use its commit
(`src-<commit>`) instead of a release number, which is what the tag would carry;
no Agent does that today.

### build

New. Replaces the switch-per-Agent script.

```yaml
# A published package
build:
  kind: npm
  package: "@openai/codex"
  version: 0.153.4
  dockerfile: docker/codex-agent/Dockerfile
```

```yaml
# A checkout, identified by commit
build:
  kind: source
  source: some_checkout           # an id in paths.source_roots
  dockerfile: docker/<agent>/Dockerfile
```

`kind: source` is declared and validated, but no Agent currently uses it. DSH
was the one candidate and it was withdrawn: its workspace build type-checks
`website`, `benchmarks` and every `tests/` directory, so compiling `lib/` inside
the image means putting all of them back into a build context that excludes them
on purpose -- several minutes per build, to support a case this repository does
not have, since it does not modify DSH's source. An Agent whose checkout does
build cleanly in an image can use this kind as it stands.

```yaml
# Already built elsewhere; nothing to do
build:
  kind: prebuilt
```

The build tool then takes an Agent id and reads its declaration:

```powershell
.\tools\eval.ps1 build codex
.\tools\eval.ps1 build codex -Version 0.160.0
```

It no longer contains a list of Agents. A new Agent is a profile and a
Dockerfile; the tool needs no edit.

### launch

Unchanged in content, and already declared: `image`, `entrypoint`, `command`,
`environment`, `workdir`, `writable_paths`.

### protocol

`adapter` selects both the in-process implementation and, today, the log parser.
Splitting those is part of this work: they are different questions, and one
Agent can change one without the other.

```yaml
protocol: responses           # responses | acp
adapter: codex                # the in-process Inspect implementation
```

### trace

New as an explicit field, previously inferred from `adapter`.

```yaml
trace:
  parser: codex               # codex | dsh-acp | generic
```

`generic` already exists as the pass-through: it emits `provider_event` for
anything it does not recognize, so an unfamiliar Agent still produces a usable
Process phase rather than an empty one.

## What is removed

### `plugins`

Added earlier in this work and wrong. It put a mechanism of one Agent — Cordis
plugin composition — into the framework's vocabulary. An Agent that has no such
mechanism cannot use the field, and an Agent that has a different one cannot
either.

An Agent's internal composition is the Agent's business. What the framework
owes it is **file delivery**: a declared way to place files where the Agent's
own startup can find them, with no opinion about what they mean.

```yaml
# The framework's whole vocabulary for this
attach:
  - from: dsh                   # a source_roots id
    to: /run-config/attach/dsh
```

DSH's runner reads `/run-config/attach/` and composes whatever it finds. A
different Agent reads it differently, or ignores it. The framework does not know
the word "plugin".

## What an Agent must provide to be evaluable

This is the contract. A profile that cannot satisfy it is refused with a message
naming the missing part, rather than failing later inside a container.

| Part | Required | If absent |
| --- | --- | --- |
| `id` | yes | profile is rejected |
| `image` (or `build` + version) | yes | nothing can start |
| `protocol` | yes | defaults to `responses` |
| a way to read the prompt | yes | `entrypoint`/`command`, or the image's own |
| `trace.parser` | no | falls back to `generic` |
| `build` | no | the image must already exist |

## Cost, and what stays hard

Honest accounting, because the previous version of this document claimed more
than it delivered.

**Cheap, once the contract exists:** adding a published CLI Agent. A profile and
a Dockerfile.

**Expensive, regardless of abstraction:**

- An Agent with a **long-lived protocol** needs a bridge. DSH's ACP runner is
  one; Claude Code's protocol would need its own. No contract removes this.
- An Agent that **cannot take a prompt non-interactively** cannot be evaluated
  without a driver that pretends to be a terminal.
- A **source build** needs its checkout's build to work in the image. The DSH
  source build needed three fixes in sequence — a missing native addon, then
  missing `lib/` output — and a fourth may be waiting. That is the state of that
  Agent's build, not of the contract.

**Not solved by this document:** whether a source build is worth its cost. It is
valuable only when a change to that Agent's source is what is being evaluated.

## Order of work

1. State `build` in the profiles; teach the build tool to read it. The script
   loses its Agent list.
2. Split `trace` from `adapter`; keep `adapter` as the in-process selector.
3. Replace `plugins` with `attach`, and move the Cordis composition into DSH's
   runner where it belongs.
4. Refuse an incomplete profile at resolution, naming the missing part.

Each step is verifiable on its own: the existing Agents must keep building,
starting and evaluating unchanged.
