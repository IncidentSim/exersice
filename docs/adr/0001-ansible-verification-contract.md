# ADR 0001: Manifest and structured Ansible verification

Status: accepted. The runner side is documented in
`tui-ansible-runner/docs/adr/0001-ansible-verifier.md`.

## Decision

- Every exercise has `manifest.yaml` next to `vm.json`:

  ```yaml
  schema_version: 1
  id: <directory name>
  description: <one line>
  environment: vm.json            # hardware, OS, packages
  actions:
    provision: {playbook: infra/playbook.yml}
    inject:    {playbook: incident/playbook.yml}
  verification:
    playbook: verification/playbook.yml
    timeout: 5m
    report:
      schema: incidentsim.verify/v1
      checks: [<ids that must be present for the exercise to pass>]
  ```

  `vm.json` keeps its hardware, packages and `playbooks` fields; the manifest and
  `vm.json` must agree. Exercises with only `vm.json` remain valid legacy exercises.
- Verification playbooks stay black-box and keep using `ansible.builtin.uri`,
  `command` and SQL through `psql`. Their results are expressed with
  `incidentsim.verify.check` (explicit `pass`/`fail`/`error` with
  `observed`/`expected`/`evidence`) instead of bare `assert`.
- The collection `collections/ansible_collections/incidentsim/verify` holds the
  machinery: `check`, `metrics` (observation window, warm-up, minimum coverage,
  aggregates incl. spread, Prometheus labels preserved), `logs` (explicit time
  scope, pattern; an unavailable source is an error, never an empty success),
  filters `dig` / `technical_error`, and the report callback that turns any task
  failure into an `error` check and writes the JSON report atomically.
- No scenario weakens its original acceptance checks; the migration is a change
  of reporting, not of thresholds.
- Verification never mutates infrastructure and never relies on global
  `failed_when: false` / `ignore_errors`.

## Consequences

Authors must list in `report.checks` every check that has to be present on success;
a check missing from a passing report makes the runner treat the run as incomplete
(technical error). Checks aborting the playbook early (first explicit failure, as
before) are allowed to leave later checks absent.
