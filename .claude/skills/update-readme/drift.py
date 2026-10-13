"""README drift: `drift.py` reports, `check` validates, `stamp [--pending FILE ...]` records (SKILL.md)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
MAP = Path(__file__).with_name("map.json")
README = ROOT / "README.md"
IMAGES = ROOT / "docs" / "images" / "readme"
SHOWN = ROOT / ".local" / "readme" / "shown.json"  # what the last report listed, for `stamp`
MAX_BYTES = 400 * 1024
PNG = b"\x89PNG\r\n\x1a\n"


def git(*args: str) -> str:
    done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return done.stdout


def lines(*args: str) -> set[str]:
    return {line for line in git(*args).split("\n") if line}


def under(path: str, prefixes: list[str]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def entries(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The `sections` or `pictures` of the map, with `{name}` in their paths filled from `vars`."""
    return [{**e, "paths": [p.format(**raw["vars"]) for p in e["paths"]]} for e in raw[key]]


def digest(path: str) -> str:
    return git("hash-object", path).strip() if (ROOT / path).is_file() else "deleted"


def relevant(raw: dict[str, Any], path: str) -> bool:
    return under(path, raw["watch"]) and not under(path, raw["ignore"])


def changed(raw: dict[str, Any], known: bool) -> list[str]:
    """Watched paths that differ from the stamp, committed or not, minus what `stamp` already saw."""
    names = lines("diff", "--name-only", raw["synced"]) if known else lines("ls-files")
    names |= lines("ls-files", "--others", "--exclude-standard")
    names |= set(raw["seen"])  # a stamped file edited again, or reverted, shows up once more
    return sorted(p for p in names if relevant(raw, p) and digest(p) != raw["seen"].get(p))


def report(raw: dict[str, Any]) -> None:
    known = (
        subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", raw["synced"] + "^{commit}"],
            capture_output=True,
        ).returncode
        == 0
    )
    paths = changed(raw, known)
    SHOWN.parent.mkdir(parents=True, exist_ok=True)
    head = git("rev-parse", "--short", "HEAD").strip()
    SHOWN.write_text(json.dumps({"head": head, "paths": {p: digest(p) for p in paths}}))
    if known:
        log = git("log", "--oneline", f"{raw['synced']}..HEAD").strip().split("\n")
        log = [line for line in log if line]
        print(f"COMMITS since {raw['synced']}: {len(log)}")
        print("\n".join(f"  {line}" for line in log))
    else:
        print(f"stamp unknown: full audit (synced={raw['synced']!r})")
    sections, pictures = entries(raw, "sections"), entries(raw, "pictures")
    print("SECTIONS")
    for e in sections:
        hits = [p for p in paths if under(p, e["paths"])]
        if hits:
            print(f"  {e['heading']} <- {', '.join(hits)}")
    print("PICTURES")
    for e in pictures:
        hits = [p for p in paths if under(p, e["paths"])]
        why = (["pending"] if e["file"] in raw["pending"] else []) + hits
        if why:
            print(f"  {e['file']} ({e['kind']}, scene {e['scene']}) <- {', '.join(why)}")
    claimed = [p for e in (*sections, *pictures) for p in e["paths"]]
    print("UNMAPPED")
    for p in paths:
        if not under(p, claimed):
            print(f"  {p}")


def check(raw: dict[str, Any]) -> list[str]:
    """Every link and picture of README.md resolves, and the pictures folder matches the map."""
    problems: list[str] = []
    text = README.read_text()
    prose = re.sub(r"```.*?```", "", text, flags=re.S)  # a fenced example may hold anything
    targets = re.findall(r"\]\(([^)\s]+)", prose)
    tags = re.findall(r"<img\b[^>]*>", prose)
    srcs = [m.group(1) for tag in tags if (m := re.search(r'\bsrc="([^"]*)"', tag))]
    for tag in tags:
        for attr in ("src", "alt", "width"):
            if not re.search(rf'\b{attr}="[^"]+"', tag):
                problems.append(f"<img> without {attr}: {tag[:80]}")
    for target in [*targets, *srcs]:
        if re.match(r"(https?:|mailto:|#)", target):
            continue
        if not (ROOT / target.split("#")[0].split("?")[0]).exists():
            problems.append(f"broken link: {target}")
    mapped = {e["file"] for e in raw["pictures"]}
    for file in sorted(f for f in IMAGES.glob("*") if not f.name.startswith(".")):
        size = file.stat().st_size
        if file.suffix != ".png" or not file.read_bytes().startswith(PNG):
            problems.append(f"{file.name} is not a PNG")
        if size > MAX_BYTES:
            problems.append(f"{file.name} is {size} bytes, over {MAX_BYTES}")
        if not any(s.endswith("/" + file.name) for s in srcs):
            problems.append(f"{file.name} is not used by README.md")
        if file.name not in mapped:
            problems.append(f"{file.name} has no `pictures` entry in map.json")
        if file.name in raw["pending"]:
            problems.append(f"{file.name} exists but is still listed as pending")
    for name in sorted(mapped - set(raw["pending"])):
        if not (IMAGES / name).exists():
            problems.append(f"{name} is in map.json, not pending, and missing")
    headings = set(re.findall(r"^#{1,6}\s+(.+?)\s*$", prose, flags=re.M))
    for e in raw["sections"]:
        if e["heading"] not in headings:
            problems.append(f"no README heading «{e['heading']}»")
    return problems


def stamp(raw: dict[str, Any], pending: list[str]) -> None:
    """Record the commit the last report covered, the pictures still stale, and the uncommitted files it listed."""
    if not SHOWN.exists():
        sys.exit("run `drift.py` first: stamp records what the last report covered")
    shown = json.loads(SHOWN.read_text())
    dirty = lines("diff", "--name-only", "HEAD") | lines(
        "ls-files", "--others", "--exclude-standard"
    )
    raw["synced"] = shown["head"]  # a commit made after the report is listed next time
    raw["pending"] = pending
    covered = {**raw["seen"], **shown["paths"]}  # only what a report listed, unchanged since
    raw["seen"] = {
        p: digest(p) for p in sorted(dirty) if relevant(raw, p) and digest(p) == covered.get(p)
    }
    text = MAP.read_text()
    for key in ("synced", "seen", "pending"):
        text = re.sub(
            rf'^  "{key}": .*$',
            lambda _, k=key: f'  "{k}": {json.dumps(raw[k])},',
            text,
            flags=re.M,
        )
    MAP.write_text(text)
    print(
        f"stamped {raw['synced']}; pending: {pending or 'none'}; {len(raw['seen'])} uncommitted files seen"
    )


def main() -> int:
    raw = json.loads(MAP.read_text())
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "report":
        report(raw)
    elif cmd == "check":
        problems = check(raw)
        print("\n".join(problems) if problems else "OK")
        return 1 if problems else 0
    elif cmd == "stamp":
        rest = sys.argv[2:]
        stamp(raw, rest[1:] if rest[:1] == ["--pending"] else [])
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
