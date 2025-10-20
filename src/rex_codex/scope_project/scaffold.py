"""Generate runtime scaffolding aligned with freshly generated specs."""

from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence

from .utils import RexContext, RexError, dump_json, ensure_dir, load_json, repo_root

_PY_MODULE_RE = re.compile(r"python\s+-m\s+([A-Za-z0-9_.-]+)")
_RUN_MODULE_RE = re.compile(r'run_module\(\s*"([^"\s]+)')


@dataclass(slots=True)
class ScaffoldResult:
    slug: str
    module: str
    created: list[Path]
    skipped: list[Path]
    auto: bool = False

    @property
    def created_rel(self) -> list[str]:
        root = repo_root()
        return [str(path.relative_to(root)) for path in self.created]

    @property
    def skipped_rel(self) -> list[str]:
        root = repo_root()
        return [str(path.relative_to(root)) for path in self.skipped]


def infer_module(slug: str, *, context: RexContext) -> str | None:
    plan_path = context.codex_ci_dir / f"component_plan_{slug}.json"
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            plan = None
        if isinstance(plan, dict):
            for text in _iter_plan_strings(plan):
                module = _extract_module_from_text(text)
                if module:
                    return module
    specs_dir = context.root / "tests" / "feature_specs" / slug
    if specs_dir.exists():
        for path in specs_dir.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            module = _extract_module_from_text(text)
            if module:
                return module
    return _fallback_module_from_slug(slug)


def scaffold_feature(
    *,
    slug: str,
    context: RexContext | None = None,
    module: str | None = None,
    force: bool = False,
    auto: bool = False,
) -> ScaffoldResult:
    context = context or RexContext.discover()
    module = module or infer_module(slug, context=context)
    if not module:
        raise RexError(
            "Unable to infer a runtime module; specify --module explicitly."
        )
    sanitized = _sanitize_module_name(module)
    if not sanitized:
        raise RexError(f"Invalid module name: {module!r}")

    target_dir = context.root / "src" / sanitized.replace(".", "/")
    ensure_dir(target_dir)

    created: list[Path] = []
    skipped: list[Path] = []

    init_path = target_dir / "__init__.py"
    main_path = target_dir / "__main__.py"

    if init_path.exists() and not force:
        skipped.append(init_path)
    else:
        init_path.write_text(_render_init_template(), encoding="utf-8")
        created.append(init_path)

    if main_path.exists() and not force:
        skipped.append(main_path)
    else:
        main_path.write_text(_render_main_template(sanitized), encoding="utf-8")
        created.append(main_path)

    result = ScaffoldResult(
        slug=slug,
        module=sanitized,
        created=created,
        skipped=skipped,
        auto=auto,
    )
    _record_scaffold(context, result, force=force)
    return result


def auto_scaffold_for_slug(
    slug: str | None,
    *,
    context: RexContext,
    verbose: bool = True,
) -> ScaffoldResult | None:
    if not slug or _env_truthy(os.environ.get("REX_DISABLE_AUTO_SCAFFOLD")):
        return None
    existing = _load_scaffold_records(context)
    if existing and not _env_truthy(os.environ.get("REX_AUTO_SCAFFOLD_ALL")):
        return None
    if any(record.get("slug") == slug for record in existing):
        return None
    module = infer_module(slug, context=context)
    if not module:
        return None
    sanitized = _sanitize_module_name(module)
    init_path = context.root / "src" / sanitized.replace(".", "/") / "__init__.py"
    if init_path.exists():
        result = ScaffoldResult(
            slug=slug,
            module=sanitized,
            created=[],
            skipped=[init_path],
            auto=True,
        )
        _record_scaffold(context, result, force=False)
        return None
    result = scaffold_feature(
        slug=slug,
        context=context,
        module=sanitized,
        force=False,
        auto=True,
    )
    if verbose and result.created:
        created = ", ".join(result.created_rel)
        print(f"[scaffold] Generated runtime scaffold for {sanitized}: {created}")
    return result


def list_known_scaffolds(context: RexContext | None = None) -> list[dict[str, object]]:
    context = context or RexContext.discover()
    return _load_scaffold_records(context)


def _iter_plan_strings(node: object) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_plan_strings(value)
    elif isinstance(node, Sequence):
        for item in node:
            yield from _iter_plan_strings(item)


def _extract_module_from_text(text: str) -> str | None:
    match = _PY_MODULE_RE.search(text)
    if match:
        return match.group(1)
    match = _RUN_MODULE_RE.search(text)
    if match:
        return match.group(1)
    return None


def _fallback_module_from_slug(slug: str) -> str:
    parts = re.split(r"[-_]+", slug)
    for part in parts:
        if part:
            return part
    return slug or "app"


def _sanitize_module_name(module: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "", module.strip())
    cleaned = cleaned.replace("-", "_")
    return cleaned.strip(".")


def _record_scaffold(
    context: RexContext,
    result: ScaffoldResult,
    *,
    force: bool,
) -> None:
    snapshot = load_json(context.rex_agent_file)
    scaffolding = snapshot.setdefault("scaffolding", {})
    records = scaffolding.setdefault("records", [])
    stamp = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload = {
        "slug": result.slug,
        "module": result.module,
        "auto": result.auto,
        "force": force,
        "created_at": stamp,
        "created": result.created_rel,
        "skipped": result.skipped_rel,
    }
    for idx, entry in enumerate(records):
        if (
            isinstance(entry, dict)
            and entry.get("slug") == result.slug
            and entry.get("module") == result.module
        ):
            records[idx] = payload
            break
    else:
        records.append(payload)
    dump_json(context.rex_agent_file, snapshot)


def _load_scaffold_records(context: RexContext) -> list[dict[str, object]]:
    snapshot = load_json(context.rex_agent_file)
    scaffolding = snapshot.get("scaffolding")
    if isinstance(scaffolding, dict):
        records = scaffolding.get("records")
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]
    return []


def _render_init_template() -> str:
    return textwrap.dedent(
        '''\
        """CLI scaffold generated by rex-codex."""

        from __future__ import annotations

        import argparse
        from collections.abc import Iterable

        DEFAULT_MESSAGE = "Hello World"


        def build_parser() -> argparse.ArgumentParser:
            parser = argparse.ArgumentParser(
                description="Emit a deterministic greeting from the command line."
            )
            parser.add_argument(
                "--message",
                help="Override the greeting text (default: Hello World).",
            )
            parser.add_argument(
                "--repeat",
                type=int,
                default=1,
                metavar="N",
                help="Number of times to repeat the greeting (default: 1).",
            )
            parser.add_argument(
                "--quiet",
                action="store_true",
                help="Suppress output while keeping a success exit code.",
            )
            return parser


        def build_greeting(message: str, repeat: int) -> str:
            return "\\n".join([message] * repeat) + "\\n"


        def main(argv: Iterable[str] | None = None) -> int:
            parser = build_parser()
            args = parser.parse_args(list(argv) if argv is not None else None)

            if args.repeat <= 0:
                parser.error("--repeat must be a positive integer")

            message = args.message or DEFAULT_MESSAGE
            greeting = build_greeting(message, args.repeat)

            if not args.quiet:
                import sys

                sys.stdout.write(greeting)
                sys.stdout.flush()
            return 0


        __all__ = ["DEFAULT_MESSAGE", "build_parser", "build_greeting", "main"]
        '''
    )


def _render_main_template(module: str) -> str:
    body = textwrap.dedent(
        """\
        \"\"\"Entry-point for ``python -m {module}``.\"\"\"

        from __future__ import annotations

        from . import main

        if __name__ == "__main__":  # pragma: no cover
            raise SystemExit(main())
        """
    )
    return body.format(module=module)


def _env_truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}
