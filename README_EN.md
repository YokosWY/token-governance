# Token-Governance (TG)

English | [简体中文](README.md)

A skill pack for measuring and reducing context cost of LLM agents. It reads per-step
usage logs, breaks the bill down to modules, then shrinks what gets resent every step.

10 skills, 28 scripts, 1 plugin. Two required dependencies: Python 3.9+ and pyyaml;
unzipping session logs also needs either the `zstd` binary or the `zstandard` module,
and Node is only needed for the guard self-check. Run `python scripts/env_check.py` on a
new machine. Runs on the configuration layer of DeepSeek Harness.

Day-to-day entry point: `python scripts/doctor.py` — one command folds schema size,
cache, cost and failures into one page with ranked actions and their estimates.

## Highlights

- **Lifetime billing** — cost = content length × remaining steps. Counting an injection once is off by ~200×, so every attribution multiplies by the steps the content survives.
- **Same-session counterfactual** — configuration is global, so two parameter sets cannot run side by side. `measure_modules.py --cf-schema --cf-catalog` replays old parameters against the same session, removing task-difficulty bias.
- **Static prefix first** — tool schemas and the skills catalog are paid every step. In one 200k-window sample: tools 230–265 → 174, schema 284–312 → 154 kB, catalog entry 42 → 9.6 kB.
- **Catalog bypass pool** — discoverable is not callable. Only `tg-router` stays resident; the other nine skills are marked `disable-model-invocation` and loaded with `read` or `/name`, so installing more does not raise the per-step floor.
- **Never rewrite history** — a cache key is an exact token prefix. Rewriting one message forces everything after it back to input price, about 50× the cache price. Post-hoc trimming is set to never fire; size is handled at write time by spilling tool output to disk.
- **Compaction is a safety valve** — each compaction pushes the whole prefix back to uncached. A 729-step session compacted 4 times showed near-zero net gain. Threshold stays high (0.8) and only fires near the window limit.
- **Three meters** — per step, per turn (one user instruction), per task. `turn_cost.py` reports median, mean and P90 so cheap steps do not hide the long tail.
- **Runtime guard** — the loop guard counts strict equality of tool name plus argument hash and appends a reminder on the 2nd/4th/6th repeat; it never blocks a call, never pops a dialog, never ends a session. Argument preview is off by default so credentials stay out of context.
- **Rollback** — `patch_snapshot.py` snapshots the patch, lists, diffs and restores; it backs up the current file and validates YAML before writing.
- **Single source of numbers** — summary tables are generated from `data/metrics.json`, and `audit_pack.py` plus the test suite fail when a number is edited by hand. `retry_patterns` and `failure_analysis` only observe; they never intercept.

## What you get

```text
$ python scripts/cost_summary.py --config config.yaml --days 7
window: last 7 days | total CNY 105.33 | 8324 calls
context 1.974 B | cache hit 99.05% | avg 237 k/call | CNY 0.0127/call

$ python scripts/measure_modules.py --session <id> --dsh-home "$DSH_HOME"
pricing: input $0.15/M cacheRead $0.003/M output $0.6/M x 7.1 (default flash price, estimate)
steps=126 turns=19 total CNY 0.719 per turn 0.0378 context 15.9M output 67k

$ python scripts/turn_cost.py --config config.yaml --days 3
per-turn CNY  median 0.0548  mean 0.0932  P90 0.22
```

## Install

Dependencies by level — when a required one is missing the scripts print a fix command
insist of a traceback:

| Level | Dependency | Purpose | If missing |
|---|---|---|---|
| required | Python ≥ 3.9 | every script | cannot run |
| required | `pyyaml` | read `config.yaml` | config-driven scripts refuse to run (intentional, see `docs/operations.md` §4) |
| required (either) | `zstd` binary or `zstandard` | decompress `session.v4.jsonl.zstd` | audit scripts say "need one of the two" |
| optional | Node 18+ | `verify_guard.mjs` self-check | that one check is skipped |

```bash
python scripts/env_check.py           # required / optional / info, with fix commands; exit 1 if a required item is missing
python tests/first_run.py             # first-run: 10 install-step commands against an empty home
```

> On macOS and most Linux distributions only `python3` exists (a bare `python` may be missing,
> or a Windows Store placeholder). Replace `python` with `python3` in every command below.

```bash
# one command (recommended): detect home, write config, copy skills, self-check
python scripts/install.py            # --dry-run / --check / --dsh-home <path> / --yes
```

Manual install:

```bash
python scripts/detect_home.py                      # prints dsh_home AND skills_dir
cp config.example.yaml config.yaml                 # paste both lines
python scripts/setup.py                            # conversation-folder root
mkdir -p "$DSH_HOME/skills" && cp -r skills/tg-* "$DSH_HOME/skills/"
python scripts/preflight.py --config config.yaml   # 0 pass / 1 review / 2 refuse
```

PowerShell: create the target first, then copy without a trailing backslash —
`New-Item -ItemType Directory -Force "$dshHome\skills" | Out-Null` followed by
`Copy-Item -Recurse -Force skills\tg-* "$dshHome\skills"`. The old one-liner with a trailing
`\` fails when `<home>\skills` does not exist yet, and without `-Force` it also fails on re-install.

Use it normally for a day: the ledger is written by cost-meter. On a fresh install the
audit exits 1 with "no readable ledger", which is expected.

Guard only: `python scripts/install_guard.py --home "$DSH_HOME" --apply` (dry-run by default,
backs up `package.json`); uninstall with `--uninstall`.

Upgrading the host rewrites the composed config; re-run
`gen_preset_override.py --window <context size>` and check `preflight` for preset and
plugin-name drift.

## Usage

| Goal | Command | Read |
|---|---|---|
| Weekly spend | `cost_summary --days 7` | daily CNY, calls, cache hit rate |
| Which module costs most | `measure_modules --session <id>` | catalog / tool schema / tool result share |
| Cost of one task | `turn_cost --days 3` | median, mean, P90 per turn |
| Why so many retries | `failure_analysis --protocol-only` | protocol-error share, top messages |
| Cache headroom | `cache_audit --days 3` | hit rate, misses per step, miss cost share |
| Did the change help | `baseline --write` then `--compare` | start context, schema, catalog |
| Is the config still aligned | `preflight --config` | preset drift, plugin-name drift |
| One-page check-up | `doctor --days 7` | size, cache, cost, failures + ranked actions |
| Who eats the schema | `schema_budget` | per-server attribution, KB budget |

## Layout

```text
token-governance/
  README.md  README_EN.md  LICENSE  CHANGELOG.md  CONTRIBUTING.md
  config.example.yaml          # three required edits, rest optional
  data/metrics.json            # single source for document numbers
  skills/                      # 10 skills (9 idle by default)
  scripts/                     # 23 scripts: check-up / measure / attribute / configure / quality
  plugins/dsh-loop-guard/      # loop guard source
  tests/                       # 152 assertions + fixtures
  docs/                        # 5 documents
  .github/workflows/ci.yml     # Windows gate + Ubuntu/macOS compatibility matrix (also a gate)
```

> `clarify-needs` moved out in v0.11.1 into its own repo:
> [dsh-clarify-tier](https://github.com/YokosWY/dsh-clarify-tier) (`skill/clarify-needs`).

## Verification

```bash
python scripts/env_check.py           # Python / pyyaml / decompressor / node / dsh_home
python scripts/audit_pack.py          # structure, references, stale words, number consistency
python tests/run_tests.py             # 152 assertions, standard library (zstandard is used when the zstd binary is absent)
python scripts/gen_metrics.py --check # tables must match data/metrics.json
```

All are read-only and fail with a non-zero exit code. The Windows CI job runs the same
commands as a merge gate, together with an Ubuntu/macOS × Python 3.10/3.12 compatibility
matrix (promoted to a gate after its first green run on 2026-10-24) and a first-run job that
replays the install steps against an empty home. A full Linux test probe runs non-blocking.

## Limits

- Other clients are not guaranteed: Claude Code, Codex and Cursor have no preset layer; isolate
  expensive tools through subagents instead (`docs/adaptation.md`).
- Scripts are DSH-specific: session logs and the cost-meter ledger are DSH formats.
- `dsh_home` resolves one way only (`scripts/tg_common.py`): `--dsh-home` > `config.yaml` >
  `$DSH_HOME` > `~/.dsh`. With two clients installed this decides which sessions and ledger
  every script reads.
- The patch is not shipped: tool trimming, pruner and compaction thresholds live in each
  machine's `cordis.patch.yml`; copying skills alone will not lower your bill.
- Preset overrides are snapshots: the composed config is rewritten on every start, so re-run
  `gen_preset_override.py` after an upgrade.
- No ledger, no conclusion: schema size and hit rate are process metrics only.
- Compaction is not a lever: low thresholds only add prefix rebuilds.

## Security and privacy

Scripts read session logs and the ledger, and upload nothing. Paths appear as-is in reports,
so review a report before publishing it. The guard defaults to `argumentsPreviewChars: 0`,
which reports argument length only; add credential-bearing tools to `exclude`. The repository
contains no credentials, `config.yaml` is gitignored and `_local/` holds machine-private notes.

## Documents

- `docs/benchmark.md` — measurement method, per-session numbers, cache-hit section
- `docs/principles.md` — twelve principles and threshold derivations
- `docs/operations.md` — loop guard and protocol discipline
- `docs/concepts.md` — terms and the six mechanisms
- `docs/adaptation.md` — other machines, other clients, no ledger

## License

MIT © 2026. See `LICENSE`.

An independent tool; not affiliated with or endorsed by any model provider. Case numbers come
from a single machine and are not a promise about your environment.
