#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: metrics
short_description: Aggregate metric samples over an observation window
description:
  - Aggregates samples collected by the playbook (for example Prometheus text parsed with the
    C(incidentsim.verify.prometheus_samples) filter, or numbers recorded by C(command) tasks).
  - Labels are preserved and matched exactly. Several matching series are an error unless I(merge=true).
  - Missing data never succeeds silently. No samples, only NaN, too few samples or too little
    coverage make the task fail (I(fail_on_unavailable=true), default) or return C(available=false).
options:
  samples:
    description: List of C({ts, value, name, labels}); C(ts) is epoch seconds.
    type: list
    elements: dict
    required: true
  window_seconds: {type: float, required: true, description: Observation window length.}
  warmup_seconds: {type: float, default: 0, description: Discarded from the start of the window.}
  end: {type: float, description: Window end (epoch seconds); defaults to the newest matching sample.}
  aggregate:
    type: str
    default: avg
    choices: [min, max, avg, last, first, spread, count, sum]
    description: Aggregate function. C(spread) is max minus min.
  name: {type: str, description: Metric name selector.}
  labels: {type: dict, default: {}, description: Exact label selector.}
  min_samples: {type: int, default: 1, description: Minimum usable samples.}
  min_coverage: {type: float, default: 0, description: Minimum fraction (0-1) of the post-warm-up window spanned by samples.}
  merge: {type: bool, default: false, description: Aggregate across all matching series.}
  fail_on_unavailable: {type: bool, default: true, description: Fail the task when data is unavailable.}
author: IncidentSim
"""

EXAMPLES = r"""
- incidentsim.verify.metrics:
    samples: "{{ session_samples }}"
    window_seconds: 120
    warmup_seconds: 60
    min_coverage: 0.8
    aggregate: spread
  register: sessions
"""

RETURN = r"""
value: {description: The aggregate, null when unavailable., type: float, returned: always}
available: {description: Whether the data satisfied the window contract., type: bool, returned: always}
reason: {description: Why the data is unavailable., type: str, returned: always}
series: {description: Aggregate per label set., type: dict, returned: when available}
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.incidentsim.verify.plugins.module_utils import observe


def main():
    module = AnsibleModule(
        argument_spec=dict(
            samples=dict(type="list", elements="dict", required=True),
            window_seconds=dict(type="float", required=True),
            warmup_seconds=dict(type="float", default=0.0),
            end=dict(type="float"),
            aggregate=dict(type="str", default="avg", choices=list(observe.AGGREGATES)),
            name=dict(type="str"),
            labels=dict(type="dict", default={}),
            min_samples=dict(type="int", default=1),
            min_coverage=dict(type="float", default=0.0),
            merge=dict(type="bool", default=False),
            fail_on_unavailable=dict(type="bool", default=True),
        ),
        supports_check_mode=True,
    )
    p = module.params
    try:
        result = observe.aggregate_window(
            p["samples"], p["window_seconds"], aggregate=p["aggregate"], name=p["name"], labels=p["labels"],
            warmup_seconds=p["warmup_seconds"], end=p["end"], min_samples=p["min_samples"],
            min_coverage=p["min_coverage"], merge=p["merge"])
    except ValueError as exc:
        module.fail_json(msg=str(exc))
    result["changed"] = False
    if not result["available"] and p["fail_on_unavailable"]:
        module.fail_json(msg="metrics unavailable: " + result["reason"], **result)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
