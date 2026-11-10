"""Turn a list of changed files, a config and a point in time into a score with reasons."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase
from posixpath import basename

from .config import DAYS, Config, PathRule
from .gitdiff import FileChange

# Built-in path categories, checked in this order after any rules from the config.
# A pattern without "/" is matched against the file name; otherwise against the
# whole path. "*" matches any run of characters, including "/".
DEFAULT_PATH_RULES = (
    PathRule("Database migrations", (
        "*migrations/*", "*migrate/*", "*alembic/versions/*", "db/schema.rb", "db/structure.sql", "*.sql",
    ), 25, "schema changes are hard to roll back"),
    PathRule("Infrastructure and deploy pipeline", (
        "*.tf", "*.tfvars", "Dockerfile*", "*.dockerfile", "docker-compose*.yml", "docker-compose*.yaml",
        "*terraform/*", "*k8s/*", "*kubernetes/*", "*helm/*", "*charts/*", "*ansible/*", "*deploy/*",
        ".github/workflows/*", ".gitlab-ci.yml", "Jenkinsfile", ".circleci/*",
    ), 20, "affects how or where the service runs"),
    PathRule("Dependencies", (
        "requirements*.txt", "pyproject.toml", "poetry.lock", "Pipfile*", "uv.lock", "package.json",
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.mod", "go.sum", "Cargo.toml",
        "Cargo.lock", "Gemfile*", "pom.xml", "build.gradle*", "composer.json", "composer.lock",
    ), 10, "third-party code changes with it"),
    PathRule("Configuration", (
        ".env*", "*.env", "*config/*", "*conf/*", "*settings*.py", "*.ini", "*.properties", "*.conf",
        "application*.yml", "application*.yaml", "config.*", "*.config.js", "*.config.ts",
    ), 15, "behaviour changes without a code change"),
)

CODE_EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".kt", ".rb", ".rs",
                   ".php", ".cs", ".c", ".cc", ".cpp", ".swift", ".scala")
TEST_PATTERNS = ("*test*", "*spec*", "*tests/*", "*__tests__/*")
SIZE_STEPS = ((1000, 25), (500, 18), (200, 12), (50, 6))  # (lines changed, points)
FILE_STEPS = ((30, 10), (10, 5))  # (files changed, points)
# Top-level directories that do not count as a separate area for blast radius.
NON_SERVICE_AREAS = {"(repo root)", "tests", "test", "docs", "doc", ".github"}
LEVELS = ("low", "medium", "high", "critical")
MAX_SCORE = 100


@dataclass(frozen=True)
class Reason:
    points: int
    factor: str
    detail: str


@dataclass
class Result:
    score: int
    level: str
    reasons: list[Reason]
    files: int
    added: int
    deleted: int
    evaluated_at: datetime
    timing_checked: bool
    notes: list[str] = field(default_factory=list)


def matches(path: str, pattern: str) -> bool:
    target = path if "/" in pattern else basename(path)
    return fnmatchcase(target, pattern)


def _sample(paths: list[str], limit: int = 3) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:limit])
    more = len(paths) - limit
    return shown + (f" and {more} more" if more > 0 else "")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _path_reasons(files: list[FileChange], cfg: Config) -> list[Reason]:
    """Each file counts toward the first rule it matches; each rule scores once."""
    rules = [*cfg.path_rules, *DEFAULT_PATH_RULES]
    hits: dict[int, list[str]] = {}
    for f in files:
        for i, rule in enumerate(rules):
            if any(matches(f.path, p) for p in rule.patterns):
                hits.setdefault(i, []).append(f.path)
                break
    reasons = []
    for i, paths in sorted(hits.items()):
        rule = rules[i]
        why = f" ({rule.reason})" if rule.reason else ""
        reasons.append(Reason(rule.points, rule.name, f"{_plural(len(paths), 'file')}: {_sample(paths)}{why}"))
    return reasons


def _size_reasons(files: list[FileChange]) -> list[Reason]:
    reasons = []
    lines = sum(f.lines for f in files)
    for floor, points in SIZE_STEPS:
        if lines >= floor:
            reasons.append(Reason(points, "Size", f"{lines} lines changed (threshold {floor})"))
            break
    for floor, points in FILE_STEPS:
        if len(files) >= floor:
            reasons.append(Reason(points, "Size", f"{len(files)} files changed (threshold {floor})"))
            break
    return reasons


def area_of(path: str, roots: tuple[str, ...]) -> str:
    """The service or top-level area a path belongs to."""
    parts = path.split("/")
    for root in roots:
        root_parts = root.split("/")
        if parts[:len(root_parts)] == root_parts and len(parts) > len(root_parts) + 1:
            return "/".join(parts[:len(root_parts) + 1])
    return parts[0] if len(parts) > 1 else "(repo root)"


def _blast_radius_reasons(files: list[FileChange], cfg: Config) -> list[Reason]:
    areas = sorted({area_of(f.path, cfg.service_roots) for f in files} - NON_SERVICE_AREAS)
    if len(areas) < 2:
        return []
    points = 5 if len(areas) == 2 else 10 if len(areas) <= 4 else 15
    return [Reason(points, "Blast radius", f"touches {len(areas)} areas: {_sample(areas, 5)}")]


def _test_reasons(files: list[FileChange]) -> list[Reason]:
    def is_test(p: str) -> bool:
        return any(matches(p, pat) for pat in TEST_PATTERNS)
    code = [f.path for f in files if f.path.endswith(CODE_EXTENSIONS) and not is_test(f.path)]
    if code and not any(is_test(f.path) for f in files):
        return [Reason(5, "No test changes", f"{_plural(len(code), 'code file')} changed, no test files touched")]
    return []


def _timing_reasons(now: datetime, cfg: Config) -> list[Reason]:
    reasons = [Reason(w.points, "Freeze window", f"{w.name} is active")
               for w in cfg.freeze_windows if w.active(now)]
    bh = cfg.business_hours
    if bh:
        stamp = f"{DAYS[now.weekday()].capitalize()} {now:%H:%M} {now.tzname()}"
        if now.weekday() not in bh.days:
            reasons.append(Reason(bh.points, "Timing", f"{stamp} is not a working day"))
        elif not bh.start <= now.time() < bh.end:
            reasons.append(Reason(bh.points, "Timing",
                                  f"{stamp} is outside business hours ({bh.start:%H:%M}-{bh.end:%H:%M})"))
    return reasons


def level_for(score: int, thresholds: dict[str, int]) -> str:
    level = "low"
    for name in LEVELS[1:]:
        if score >= thresholds[name]:
            level = name
    return level


def score_change(files: list[FileChange], cfg: Config, now: datetime) -> Result:
    """Score a change. `now` may be naive (taken as config-timezone time) or aware."""
    now = now.replace(tzinfo=cfg.timezone) if now.tzinfo is None else now.astimezone(cfg.timezone)
    reasons: list[Reason] = []
    notes: list[str] = []
    if files:
        reasons += _path_reasons(files, cfg) + _size_reasons(files)
        reasons += _blast_radius_reasons(files, cfg) + _test_reasons(files)
        # A freeze only matters if there is something to ship.
        reasons += _timing_reasons(now, cfg)
    else:
        notes.append("No files changed between the two refs.")
    if not cfg.has_timing:
        notes.append("Timing was not scored: no freeze_windows or business_hours in the config.")
    reasons.sort(key=lambda r: -r.points)
    raw = sum(r.points for r in reasons)
    if raw > MAX_SCORE:
        notes.append(f"Raw points total {raw}; the score is capped at {MAX_SCORE}.")
    score = min(raw, MAX_SCORE)
    return Result(score, level_for(score, cfg.thresholds), reasons, len(files),
                  sum(f.added for f in files), sum(f.deleted for f in files),
                  now, cfg.has_timing, notes)
