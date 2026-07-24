# Parity Fixtures

TASK-029 creates this group and runner hook so later tasks can add paired
Python/Java cases. TASK-031/TASK-032 cover equivalent verdicts. TASK-036 adds
an equivalent one-hop `sink -> helper` graph in both languages and asserts the
same reachability structure. TASK-037 adds paired indirect-impact verdict
fixtures for a direct sink dependency, an unrelated change, and the configured
hop boundary.
