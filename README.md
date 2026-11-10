# change-risk-scorer

Score the risk of a git change by what it touches, how big it is and when it ships, and explain the score in Markdown you can drop into a pull request.

**Status:** working first version (0.1.0). The command-line scorer works today: path-based detection of migrations, infra, config and dependency changes, size and blast-radius scoring, freeze windows and business hours from a config file, and Markdown, text or JSON output. A composite GitHub Action wraps the CLI and is run in this repository's own CI. Incident history is not built, and the action's comment-posting step has not yet been tried on a live pull request.

## Why

Risky changes look the same as safe ones in a pull request list until they cause an incident. A schema migration shipped on a Friday afternoon during a change freeze deserves more eyes than a typo fix, and this tool says so, with its reasons.

## Install

Requires Python 3.10 or newer and git. The only dependency is PyYAML.

```bash
pip install git+https://github.com/narayana006/change-risk-scorer
```

## Quick start

The repository includes a script that builds a small throwaway git repository with a `main` and a `feature` branch, and an example config with a freeze window:

```bash
git clone https://github.com/narayana006/change-risk-scorer
cd change-risk-scorer
pip install -e .
bash examples/make-demo-repo.sh /tmp/risk-demo
change-risk-scorer --repo /tmp/risk-demo --base main --head feature \
  --config examples/change-risk.yml --now 2026-11-27T16:30
```

Output (Markdown):

```markdown
## Change risk: CRITICAL (100/100)

`main...feature`: 5 files changed, +9 / -5 lines. Evaluated Fri 2026-11-27 16:30 EST.

| Points | Factor | Why |
|---:|---|---|
| +40 | Freeze window | Thanksgiving week change freeze is active |
| +25 | Database migrations | 1 file: `db/migrations/002_add_currency.sql` (schema changes are hard to roll back) |
| +20 | Payments service | 1 file: `services/payments/charge.py` (handles money; needs a second reviewer) |
| +20 | Infrastructure and deploy pipeline | 1 file: `deploy/k8s/orders.yaml` (affects how or where the service runs) |
| +15 | Freeze window | Friday afternoon is active |
| +10 | Dependencies | 1 file: `requirements.txt` (third-party code changes with it) |
| +10 | Blast radius | touches 4 areas: `db`, `deploy`, `services/orders`, `services/payments` |
| +5 | No test changes | 2 code files changed, no test files touched |

- Raw points total 145; the score is capped at 100.
```

`--now` pins the evaluation time so the example is reproducible; leave it out to use the current time.

In your own repository, from a feature branch:

```bash
change-risk-scorer --base origin/main
```

## How the score is built

Points from each factor are added and capped at 100. Every factor that scored is listed with its reason, highest first.

| Factor | Points | Triggered by |
|---|---:|---|
| Database migrations | 25 | `migrations/`, `migrate/`, `alembic/versions/`, `*.sql`, `db/schema.rb` |
| Infrastructure and deploy pipeline | 20 | Terraform, Dockerfiles, Compose files, `k8s/`, `helm/`, `charts/`, `deploy/`, CI workflow files |
| Configuration | 15 | `.env*`, `config/`, `conf/`, settings modules, `*.ini`, `*.properties`, `application*.yml` |
| Dependencies | 10 | `requirements*.txt`, `pyproject.toml`, `package.json`, lock files, `go.mod`, `Cargo.toml`, `pom.xml` and similar |
| Your own path rules | you choose | `paths` in the config; checked before the built-in rules |
| Size | 6 / 12 / 18 / 25 | 50 / 200 / 500 / 1000 lines changed (added plus deleted) |
| Size | 5 / 10 | 10 / 30 files changed |
| Blast radius | 5 / 10 / 15 | 2 / 3-4 / 5 or more areas touched |
| No test changes | 5 | code files changed and no test file touched |
| Freeze window | 40 by default | a window from the config is active at evaluation time |
| Timing | 10 by default | evaluation time is outside the configured business hours or working days |

Each file counts toward the first path rule it matches, and each rule scores once however many files match. An "area" is a top-level directory, or a child of one of the `services.roots` directories in the config; `tests`, `docs` and `.github` are not counted as areas.

Levels: `low` below 25, `medium` from 25, `high` from 50, `critical` from 75. The thresholds can be changed in the config.

The diff is taken from the merge base (`git diff base...head`), so commits that landed on the base branch after you branched are not counted against you.

## Configuration

Timing is only scored when a config provides freeze windows or business hours. The scorer looks for `.change-risk.yml`, `.change-risk.yaml` or `.change-risk.json` in the repository root, or takes a path with `--config`. See [examples/change-risk.yml](examples/change-risk.yml) for a commented example.

```yaml
timezone: America/New_York

business_hours:
  start: "09:00"
  end: "17:00"
  days: [mon, tue, wed, thu, fri]

freeze_windows:
  - name: Thanksgiving week change freeze   # dated window
    start: "2026-11-25T00:00"
    end: "2026-11-30T09:00"
    points: 40
  - name: Friday afternoon                  # weekly window
    days: [fri]
    start_time: "15:00"
    end_time: "23:59"
    points: 15

paths:
  - name: Payments service
    patterns: ["services/payments/*"]
    points: 20
    reason: handles money; needs a second reviewer

services:
  roots: [services]

thresholds:
  medium: 25
  high: 50
  critical: 75
```

In patterns, `*` matches any characters including `/`. A pattern with no `/` is compared with the file name only; any other pattern is compared with the whole path. Unknown config keys are rejected with an error rather than ignored.

## Command-line options

| Option | Default | Meaning |
|---|---|---|
| `--repo` | `.` | Path to the git repository |
| `--base` | `origin/main` | Ref the change merges into |
| `--head` | `HEAD` | Ref being scored |
| `--config` | auto-detected | YAML or JSON config file |
| `-f`, `--format` | `markdown` | `markdown`, `text` or `json` |
| `-o`, `--output` | none | Also write the report to a file |
| `--fail-on` | `never` | Exit 1 when the level is `medium`, `high` or `critical` or worse |
| `--now` | current time | ISO time to evaluate at; without an offset it is read in the config timezone |

Exit codes: `0` below the `--fail-on` level, `1` at or above it, `2` for errors such as an unknown ref or an invalid config.

## GitHub Action

The repository root is a composite action that installs the CLI and runs it on the checked-out code. It needs full history to find the merge base.

```yaml
jobs:
  risk:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - id: risk
        uses: narayana006/change-risk-scorer@main
        with:
          fail-on: critical
      - run: echo "Risk is ${{ steps.risk.outputs.level }} (${{ steps.risk.outputs.score }})"
```

The report is added to the job summary. Outputs are `score`, `level` and `report` (path to the Markdown file). Inputs are `base` (defaults to the pull request base branch, or `HEAD~1` on other events), `head`, `config`, `fail-on`, `comment` and `token`.

Setting `comment: "true"` on a `pull_request` event posts the report with `gh pr comment` and needs `permissions: pull-requests: write`. This step is implemented but has not yet been exercised on a real pull request, so treat it as experimental. The rest of the action runs in this repository's CI on every push. There is no `v1` tag yet; pin to `main` or a commit SHA.

## Features

- [x] Blast-radius estimation from touched paths and services
- [x] Config and infra change detection (plus migrations and dependencies)
- [x] Time-of-day / freeze-window awareness from a YAML or JSON config
- [x] Markdown report with score and reasons, sized for a PR comment
- [x] Composite GitHub Action with job summary and outputs
- [ ] Recent incidents in the same area
- [ ] PR comment posting verified end to end, and updating one comment instead of adding new ones

## Limitations

- The score is a heuristic built from fixed point values. It is a prompt for attention, not a prediction of failure.
- Only file paths and line counts are read. The content of the diff is not analysed, so a one-line change to a critical constant scores the same as any other one-line change.
- Renames are counted as a delete plus an add.
- Timing is judged at the moment the scorer runs (or `--now`), not at the eventual merge or deploy time.
- Blast radius is based on directories, not on real service dependencies.
- Weekly freeze windows cannot span midnight; use two entries.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Contributing

Issues and pull requests are welcome, especially path patterns for ecosystems that are missed today and examples of changes that were scored badly.

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
