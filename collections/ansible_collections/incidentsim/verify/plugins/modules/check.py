#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: check
short_description: Record one verification check with an explicit outcome
description:
  - Evaluates a single acceptance check and returns it as C(incidentsim_check).
  - A failed or errored check fails the task, so the playbook stops like an assert would.
    The report callback records the outcome in the JSON report.
options:
  id:
    description: Stable check identifier (a-z, 0-9, C(.), C(_), C(-)).
    type: str
    required: true
  description:
    description: What the check verifies.
    type: str
  passed:
    description: Verdict of the check; mutually exclusive with I(status).
    type: bool
  status:
    description: Explicit outcome.
    type: str
    choices: [pass, fail, error]
  error:
    description: Technical problem that prevented evaluation. When non-empty the check is C(error).
    type: str
  observed:
    description: What was observed.
    type: raw
  expected:
    description: What was expected.
    type: raw
  evidence:
    description: Supporting data. Secrets are redacted and the text is size bounded.
    type: raw
author: IncidentSim
"""

EXAMPLES = r"""
- incidentsim.verify.check:
    id: app.health
    description: The application answers /healthz
    passed: "{{ probe | incidentsim.verify.dig('status') == 200 }}"
    error: "{{ probe | incidentsim.verify.technical_error }}"
    observed: "{{ probe | incidentsim.verify.dig('status') }}"
    expected: 200
"""

RETURN = r"""
incidentsim_check:
  description: The recorded check.
  type: dict
  returned: always
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.incidentsim.verify.plugins.module_utils import report


def main():
    module = AnsibleModule(
        argument_spec=dict(
            id=dict(type="str", required=True),
            description=dict(type="str", default=""),
            passed=dict(type="bool"),
            status=dict(type="str", choices=list(report.STATUSES)),
            error=dict(type="str", default=""),
            observed=dict(type="raw"),
            expected=dict(type="raw"),
            evidence=dict(type="raw"),
        ),
        mutually_exclusive=[("passed", "status")],
        supports_check_mode=True,
    )
    p = module.params
    try:
        check = report.evaluate(
            p["id"], passed=p["passed"], status=p["status"], error=p["error"],
            description=p["description"], observed=p["observed"], expected=p["expected"], evidence=p["evidence"],
        )
    except report.CheckError as exc:
        module.fail_json(msg=str(exc))
    result = dict(changed=False, incidentsim_check=check)
    if check["status"] != "pass":
        result["msg"] = "check %s %s: expected %s, observed %s" % (
            check["id"], check["status"], check.get("expected", "-"), check.get("observed", check.get("evidence", "-")))
        module.fail_json(**result)
    result["msg"] = "check %s passed" % check["id"]
    module.exit_json(**result)


if __name__ == "__main__":
    main()
