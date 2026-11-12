# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
name: report
type: aggregate
short_description: Write the incidentsim.verify JSON report
description:
  - Collects every C(incidentsim.verify.check) result and records every other task failure or
    unreachable host as an C(error) check, so a crashed task can never turn into a pass.
  - Tasks failing with C(ignore_errors) are the playbook's own business and are not recorded.
  - Writes the report atomically when the playbook ends. Without the C(INCIDENTSIM_*) variables
    the callback only prints a summary.
requirements:
  - Enable with C(ANSIBLE_CALLBACKS_ENABLED=incidentsim.verify.report).
options:
  report_path:
    description: Where to write the report.
    env: [{name: INCIDENTSIM_REPORT_PATH}]
    type: str
  scenario:
    description: Exercise name stored in the report.
    env: [{name: INCIDENTSIM_SCENARIO}]
    type: str
  attempt_id:
    description: Verification attempt identifier stored in the report.
    env: [{name: INCIDENTSIM_ATTEMPT_ID}]
    type: str
"""

import datetime

from ansible.plugins.callback import CallbackBase
from ansible_collections.incidentsim.verify.plugins.module_utils import report


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "incidentsim.verify.report"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, *args, **kwargs):
        super(CallbackModule, self).__init__(*args, **kwargs)
        self.checks = []
        self.ids = set()
        self.started = _now()
        self.written = False

    def set_options(self, task_keys=None, var_options=None, direct=None):
        super(CallbackModule, self).set_options(task_keys=task_keys, var_options=var_options, direct=direct)
        self.report_path = self.get_option("report_path")
        self.scenario = self.get_option("scenario")
        self.attempt_id = self.get_option("attempt_id")

    # -- recording ---------------------------------------------------------
    def _add(self, check):
        check = dict(check)
        check["id"] = report.unique_id(check["id"], self.ids)
        self.ids.add(check["id"])
        self.checks.append(check)

    def _record(self, result, ignore_errors=False, failed=False):
        # Never let a callback bug hide a failure: it becomes an error check.
        try:
            self._record_unsafe(result, ignore_errors, failed)
        except Exception as exc:  # pylint: disable=broad-except
            self._add(report.make_check("callback.internal", "error", description="report callback failed",
                                        evidence="%s: %s" % (type(exc).__name__, exc)))

    def _record_unsafe(self, result, ignore_errors=False, failed=False):
        data = result._result
        check = data.get("incidentsim_check")
        if isinstance(check, dict) and "id" in check:
            self._add(check)
            return
        if "results" in data and isinstance(data["results"], list):
            return  # loop summary; every item was reported by the item callbacks
        failed = failed or data.get("failed") or data.get("unreachable")
        if not failed or ignore_errors:
            return
        task = result._task
        raw_id = task.args.get("id") if isinstance(task.args, dict) else None
        if not (isinstance(raw_id, str) and report.CHECK_ID.match(raw_id)):
            raw_id = "task." + report.slug(task.get_name())
        evidence = data.get("msg") or data.get("stderr") or "task failed"
        if data.get("unreachable"):
            evidence = "unreachable: %s" % evidence
        self._add(report.make_check(raw_id, "error", description="technical failure of task: %s" % task.get_name(),
                                    evidence=evidence))

    def v2_runner_on_ok(self, result):
        self._record(result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._record(result, ignore_errors, failed=True)

    def v2_runner_on_unreachable(self, result):
        self._record(result, failed=True)

    def v2_runner_item_on_ok(self, result):
        self._record(result)

    def v2_runner_item_on_failed(self, result):
        self._record(result, ignore_errors=bool(result._task.ignore_errors), failed=True)

    # -- finishing -----------------------------------------------------------
    def v2_playbook_on_stats(self, stats):
        hosts = sorted(stats.processed.keys())
        broken = any(stats.summarize(h).get("failures") or stats.summarize(h).get("unreachable") for h in hosts)
        if broken and not any(c["status"] != "pass" for c in self.checks):
            self._add(report.make_check("playbook.run", "error", description="playbook failed before reporting a check",
                                        evidence="a host failed or was unreachable without a recorded task result"))
        data = report.build_report(self.scenario or "", self.attempt_id or "", self.checks, self.started, _now())
        for check in data["checks"]:
            self._display.display("  [%s] %s" % (check["status"].upper(), check["id"]))
        if self.report_path and self.scenario and self.attempt_id:
            report.write_report_atomic(self.report_path, data)
            self.written = True
            self._display.display("incidentsim.verify report: %s (%s)" % (data["status"], self.report_path))
