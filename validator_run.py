# Arm 06: baseline agent + a runtime validator in front of the database.
# The agent, prompt and settings are the same as 05 (pilot_vllm.py runs them).
# Only change: before a write tool runs, policy_checker checks the data rules
# from the policy graph. If one fails, the call is blocked and the agent gets
# an error message instead. Every block is logged to a .jsonl file.
import json
import os
import runpy
import sys
import threading

import policy_checker as pc
from tau_bench.envs.base import Env
from tau_bench.types import EnvInfo, EnvResponse

GRAPH = pc.load_graph()                      # stops here if graph and code differ
LOG = os.environ.get("VALIDATOR_LOG", "validator_blocks.jsonl")
_lock = threading.Lock()

_real_reset, _real_step, _real_reward = Env.reset, Env.step, Env.calculate_reward


def reset(self, task_index=None):
    self._pc_hist = []                       # write calls that ran in this dialogue
    self._pc_off = False
    return _real_reset(self, task_index)


def calculate_reward(self):
    # tau-bench scores by replaying the answer key through step().
    # The validator must not touch that replay, or it changes the answer key.
    self._pc_off = True
    try:
        return _real_reward(self)
    finally:
        self._pc_off = False


def step(self, action):
    name, kw = action.name, action.kwargs
    if name in pc.WRITE_TOOLS and not self._pc_off:
        try:
            v = pc.check(name, kw, self.data, self._pc_hist, GRAPH)
        except Exception as e:               # a checker bug must never crash a run
            v = []
            print(f"[validator] checker error on {name}: {e}", file=sys.stderr)
        if v:
            self.actions.append(action)
            with _lock, open(LOG, "a") as f:
                f.write(json.dumps({"task_id": self.task_index, "tool": name, "kwargs": kw,
                                    "violations": v}) + "\n")
            return EnvResponse(observation=pc.message(v), reward=0, done=False,
                               info=EnvInfo(task=self.task, source=name))
    res = _real_step(self, action)
    if name in pc.WRITE_TOOLS and not self._pc_off and not str(res.observation).startswith("Error"):
        self._pc_hist.append({"name": name, "kwargs": kw})
    return res


Env.reset, Env.step, Env.calculate_reward = reset, step, calculate_reward

# same agent as the baseline: pilot_vllm.py patches litellm and calls run.main()
here = os.path.dirname(os.path.abspath(__file__))
runpy.run_path(os.path.join(here, "pilot_vllm.py"), run_name="__main__")
