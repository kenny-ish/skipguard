# skipguard

A green test step does not mean the tests ran. `go test` and pytest exit 0 when the integration suite skipped itself because the database was not there, when every test was skipped, or when a `-run` / `-k` filter matched nothing. skipguard reads the runner's machine-readable report and fails the step when the tests you care about did not actually run and pass.

It reads `go test -json` output and JUnit XML (pytest `--junitxml`, gotestsum, jest-junit, Maven Surefire and most other runners can write it). Python 3.10+, standard library only.

## Example

A package with one test that needs a database and skips without it. Real output, Go 1.22 on Linux:

```
$ go test ./... ; echo "exit $?"
ok  	example.com/store	0.002s
exit 0

$ go test -json ./... | python3 ../skipguard.py --require TestMigrate ; echo "exit $?"
skipguard: 1 passed, 0 failed, 1 skipped
skipguard: required test 'TestMigrate' did not pass (example.com/store.TestMigrate: skipped)
exit 1

$ DB_PORT=3307 go test -count=1 -json ./... | python3 ../skipguard.py --require TestMigrate ; echo "exit $?"
skipguard: 2 passed, 0 failed, 0 skipped
exit 0
```

A typo in a test filter, which `go test` reports as success:

```
$ go test -json -run TestMigation ./... | python3 ../skipguard.py ; echo "exit $?"
skipguard: 0 passed, 0 failed, 0 skipped
skipguard: only 0 test(s) passed, expected at least 1
exit 1
```

## Usage

```
go test -json ./... | python skipguard.py [options]
python skipguard.py report.xml [options]
```

- `--require PATTERN`: this test must have run and passed. Glob, repeatable. A pattern matches the full name (`example.com/store.TestMigrate`, `tests.test_db.test_migrate`) or its last part (`TestMigrate`, `TestGate/*` for subtests).
- `--no-skips`: fail on any skipped test, printing the skip message when the report has one.
- `--allow-skip PATTERN`: with `--no-skips`, a skip that is expected (for example a Windows-only test on Linux). Repeatable.
- `--min-passed N`: at least N tests must pass (default 1, which catches the "nothing ran" case).

Exit codes: 0 when everything holds, 1 when a check fails, 2 when the report cannot be read.

skipguard does not replace the runner's own exit code. In a pipeline, keep `set -o pipefail` so a failing `go test` still fails the step.

## In CI

```yaml
- run: |
    set -o pipefail
    go test -json ./internal/storage/... | python skipguard.py --require 'TestDolt*' --no-skips --allow-skip 'TestWindows*'
```

```yaml
- run: |
    pytest --junitxml=report.xml tests/integration
    python skipguard.py report.xml --no-skips
```

## Tests

```
python -m unittest -v test_skipguard
```
