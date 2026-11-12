# incidentsim.verify

Machinery for exercise verification playbooks. Playbooks compose observations
(`uri`, `command`, `psql`) with these building blocks; they contain no report logic.

| Plugin | Purpose |
| --- | --- |
| `incidentsim.verify.check` | One check: `pass` / `fail` / `error` with `observed`, `expected`, `evidence`. A non-pass check fails the task. |
| `incidentsim.verify.metrics` | Aggregate samples over a window (warm-up, minimum samples/coverage, `min max avg last first spread count sum`). Labels are preserved. Missing/NaN data is never a pass. |
| `incidentsim.verify.logs` | Pattern in an explicit time scope; an unreadable or empty source is `unavailable`, not an empty success. Supports a resume cursor. |
| filters `dig`, `technical_error`, `prometheus_samples` | Safe lookups, classification of verifier faults vs. application symptoms, Prometheus text parsing. |
| callback `incidentsim.verify.report` | Records every check and every other task failure as an `error` check and writes the report atomically. |

Strictness: series identity is a canonical JSON of the label set (no delimiter
collisions); malformed Prometheus lines or label sets, non-finite window, warm-up,
`end`, coverage, counts or timestamps are rejected; an aggregate that is not finite
(overflow) is `unavailable`; NaN/Inf/boolean samples never contribute. `logs` treats
non-blank lines without a timestamp as a risk of hidden errors: `expect: absent`
(and an unmatched `present`) is `unavailable` unless `allow_unparsed: true`.
Tasks with `no_log` contribute only a check's id and verdict to the report, never
details. Tasks that carry credentials (`PGPASSWORD`) must set `no_log: true`.

The runner enables the callback and provides `INCIDENTSIM_SCENARIO`,
`INCIDENTSIM_ATTEMPT_ID` and `INCIDENTSIM_REPORT_PATH`:

```sh
ANSIBLE_COLLECTIONS_PATH=collections \
ANSIBLE_CALLBACKS_ENABLED=incidentsim.verify.report \
INCIDENTSIM_SCENARIO=nginx-wrong-api INCIDENTSIM_ATTEMPT_ID=ABCDEFGHIJKLMNOPQRSTUVWXYZ \
INCIDENTSIM_REPORT_PATH=/tmp/report.json \
ansible-playbook --inventory localhost, --connection local nginx-wrong-api/verification/playbook.yml
```

Tests: `pytest collections/ansible_collections/incidentsim/verify/tests`.
