#!/usr/bin/env python3
"""Fail a CI step when the tests it reports as green did not actually run.

    go test -json ./... | python skipguard.py --require TestMigrate
    pytest --junitxml=report.xml; python skipguard.py report.xml --no-skips

A test runner exits 0 when every test was skipped, when a filter matched
nothing, or when an integration suite could not find its database and
skipped itself. skipguard reads the runner's machine-readable report
(`go test -json` output or JUnit XML) and checks what really ran.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

OUTCOMES = ("passed", "failed", "skipped")


@dataclass
class Report:
    # test name -> passed, failed or skipped (the last outcome seen wins)
    tests: dict[str, str] = field(default_factory=dict)
    # test name -> skip reason, when the report has one
    reasons: dict[str, str] = field(default_factory=dict)
    # test name -> the name the runner itself reported, without the package or class
    short: dict[str, str] = field(default_factory=dict)

    def names(self, outcome: str) -> list[str]:
        return sorted(n for n, o in self.tests.items() if o == outcome)


def parse_go_json(lines) -> Report:
    """`go test -json` output: one JSON event per line."""
    report = Report()
    output: dict[str, list[str]] = {}
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue  # build output or other noise mixed into the stream
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        test = ev.get("Test")
        if not test:
            continue  # package-level events
        name = f"{ev.get('Package', '')}.{test}".lstrip(".")
        action = ev.get("Action")
        if action == "output":
            output.setdefault(name, []).append(ev.get("Output", ""))
        elif action in ("pass", "fail", "skip"):
            report.tests[name] = {"pass": "passed", "fail": "failed", "skip": "skipped"}[action]
            report.short[name] = test
            if action == "skip":
                lines_out = [o.strip() for o in output.get(name, []) if o.strip()]
                # the last line before "--- SKIP" is usually the t.Skip message
                msgs = [o for o in lines_out if not o.startswith(("=== ", "--- "))]
                if msgs:
                    report.reasons[name] = msgs[-1]
    return report


def parse_junit(text: str) -> Report:
    """JUnit XML, as written by pytest --junitxml, gotestsum, jest-junit, maven and others."""
    report = Report()
    root = ET.fromstring(text)
    for case in root.iter("testcase"):
        name = case.get("name", "")
        cls = case.get("classname")
        full = f"{cls}.{name}" if cls else name
        report.short[full] = name
        if case.find("failure") is not None or case.find("error") is not None:
            report.tests[full] = "failed"
        elif (skip := case.find("skipped")) is not None:
            report.tests[full] = "skipped"
            reason = skip.get("message") or (skip.text or "").strip()
            if reason:
                report.reasons[full] = reason.splitlines()[0]
        else:
            report.tests[full] = "passed"
    return report


def parse(text: str) -> Report:
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return parse_junit(text)
    return parse_go_json(text.splitlines())


def matches(name: str, short: str, pattern: str) -> bool:
    # a pattern matches the full name or the test's own name (Go: TestX/sub, JUnit: the name
    # attribute). The full name cannot simply be split at its last dot: a subtest or parameter
    # id such as TestParse/v1.2 or test_x[1.5] contains dots of its own.
    return fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(short, pattern)


def check(report: Report, require: list[str], no_skips: bool, allow_skip: list[str], min_passed: int) -> list[str]:
    problems = []
    passed = report.names("passed")
    if len(passed) < min_passed:
        problems.append(f"only {len(passed)} test(s) passed, expected at least {min_passed}")
    for pattern in require:
        hits = {n: o for n, o in report.tests.items() if matches(n, report.short.get(n, n), pattern)}
        if not hits:
            problems.append(f"required test {pattern!r} did not run at all")
        elif not any(o == "passed" for o in hits.values()):
            outcomes = ", ".join(f"{n}: {o}" for n, o in sorted(hits.items()))
            problems.append(f"required test {pattern!r} did not pass ({outcomes})")
    if no_skips:
        for name in report.names("skipped"):
            if not any(matches(name, report.short.get(name, name), p) for p in allow_skip):
                why = report.reasons.get(name)
                problems.append(f"skipped: {name}" + (f" ({why})" if why else ""))
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("report", nargs="?", type=Path, help="report file (default: read stdin)")
    p.add_argument("--require", action="append", default=[], metavar="PATTERN",
                   help="a test that must have run and passed (glob; repeatable)")
    p.add_argument("--no-skips", action="store_true", help="fail on any skipped test")
    p.add_argument("--allow-skip", action="append", default=[], metavar="PATTERN",
                   help="with --no-skips, a skip that is fine (glob; repeatable)")
    p.add_argument("--min-passed", type=int, default=1, help="minimum number of passed tests (default 1)")
    args = p.parse_args(argv)

    text = args.report.read_text(encoding="utf-8") if args.report else sys.stdin.read()
    try:
        report = parse(text)
    except ET.ParseError as e:
        print(f"skipguard: cannot parse the report as JUnit XML: {e}", file=sys.stderr)
        return 2
    counts = ", ".join(f"{len(report.names(o))} {o}" for o in OUTCOMES)
    print(f"skipguard: {counts}")
    problems = check(report, args.require, args.no_skips, args.allow_skip, args.min_passed)
    for line in problems:
        print(f"skipguard: {line}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
