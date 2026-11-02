# Run tau-bench for 09: the AGENT is Qwen on my vLLM server (base model or a
# LoRA adapter), the USER is the same Gemini simulator as every other arm.
# Every call is logged (who, seconds, tokens) to CALL_LOG.
# DEEP_VALIDATOR=1 adds the 06G runtime validator in front of the database.
import json
import os
import sys
import threading
import time

import litellm
from settings_gemini import MODEL as USER_MODEL, NUM_RETRIES, CALL_TIMEOUT, USER_TEMPERATURE
from settings_deep import AGENT_THINKING, AGENT_MAX_TOKENS

litellm.suppress_debug_info = True
KEY = os.environ["GEMINI_API_KEY"]
VLLM = os.environ.get("VLLM_BASE", "http://localhost:8000/v1")
LOG = os.environ.get("CALL_LOG", "deep_calls.jsonl")
_lock = threading.Lock()
_real = litellm.completion


def routed(*args, **kwargs):
    who = "agent" if kwargs.get("tools") else "user"
    if who == "agent":
        kwargs["custom_llm_provider"] = "hosted_vllm"
        kwargs["api_base"] = VLLM
        kwargs["api_key"] = "none"
        extra = dict(kwargs.get("extra_body") or {})
        extra["chat_template_kwargs"] = {"enable_thinking": AGENT_THINKING}
        kwargs["extra_body"] = extra
        kwargs.setdefault("max_tokens", AGENT_MAX_TOKENS)
    else:
        kwargs["model"] = USER_MODEL
        kwargs["custom_llm_provider"] = "gemini"
        kwargs["api_key"] = KEY
        kwargs["temperature"] = USER_TEMPERATURE
        kwargs.setdefault("num_retries", NUM_RETRIES)
    kwargs["timeout"] = CALL_TIMEOUT
    t0 = time.time()
    res = _real(*args, **kwargs)
    u = res.usage
    with _lock, open(LOG, "a") as f:
        f.write(json.dumps({"who": who, "sec": round(time.time() - t0, 2),
                            "prompt": u.prompt_tokens, "completion": u.completion_tokens}) + "\n")
    msg = res.choices[0].message
    if msg.content is None and not msg.tool_calls:
        msg.content = ""                        # empty reply = normal fail, not a crash
    return res


litellm.completion = routed
import tau_bench.envs.user as _user
_user.completion = lambda *a, **k: litellm.completion(*a, **k)


def install_validator():
    """Same validator as 06G (copied, without its runpy line)."""
    import policy_checker as pc
    from tau_bench.envs.base import Env
    from tau_bench.types import EnvInfo, EnvResponse
    graph = pc.load_graph()
    vlog = os.environ.get("VALIDATOR_LOG", "validator_blocks.jsonl")
    real_reset, real_step, real_reward = Env.reset, Env.step, Env.calculate_reward

    def reset(self, task_index=None):
        self._pc_hist, self._pc_off = [], False
        return real_reset(self, task_index)

    def calculate_reward(self):
        self._pc_off = True                      # never touch the answer-key replay
        try:
            return real_reward(self)
        finally:
            self._pc_off = False

    def step(self, action):
        name, kw = action.name, action.kwargs
        if name in pc.WRITE_TOOLS and not self._pc_off:
            try:
                v = pc.check(name, kw, self.data, self._pc_hist, graph)
            except Exception as e:
                v = []
                print(f"[validator] checker error on {name}: {e}", file=sys.stderr)
            if v:
                self.actions.append(action)
                with _lock, open(vlog, "a") as f:
                    f.write(json.dumps({"task_id": self.task_index, "tool": name, "kwargs": kw,
                                        "violations": v}) + "\n")
                return EnvResponse(observation=pc.message(v), reward=0, done=False,
                                   info=EnvInfo(task=self.task, source=name))
        res = real_step(self, action)
        if name in pc.WRITE_TOOLS and not self._pc_off and not str(res.observation).startswith("Error"):
            self._pc_hist.append({"name": name, "kwargs": kw})
        return res

    Env.reset, Env.step, Env.calculate_reward = reset, step, calculate_reward


if os.environ.get("DEEP_VALIDATOR") == "1":
    install_validator()

sys.argv[0] = "run.py"
from run import main
main()
