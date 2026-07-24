# tests/fixtures/

TASK-004 golden fixtures. Each case stores git diff surface output only:

- `name-status.txt`: lines shaped like `git diff --name-status`
- `numstat.txt`: optional lines shaped like `git diff --numstat`
- `change-intent.yaml`: case-local intent where the gate needs it

Cases: good, out-of-scope, forbidden, frozen, protected, watched.
