# run tau-bench on Gemini: with my API key, or Colab's built-in Gemini
# every model call goes through logged(): it picks the backend,
# retries on rate limits, and writes tokens and seconds to a .jsonl file
import json
import os
import sys
import threading
import time

import litellm
from settings_gemini import NUM_RETRIES, CALL_TIMEOUT, PROXY_PREFIX, USER_TEMPERATURE, BACKEND

litellm.suppress_debug_info = True
if BACKEND == "colab":
    BASE = os.environ["COLAB_PROXY_BASE"]  # set by the notebook from google.colab.ai
    KEY = os.environ["COLAB_PROXY_KEY"]
else:
    KEY = os.environ["GEMINI_API_KEY"]     # set by the notebook from Colab Secrets
LOG = os.environ.get("CALL_LOG", "gemini_calls.jsonl")
_lock = threading.Lock()
_real = litellm.completion


def logged(*args, **kwargs):
    who = "agent" if kwargs.get("tools") else "user"
    if BACKEND == "colab":
        m = kwargs.get("model", "")
        if not m.startswith(PROXY_PREFIX):
            kwargs["model"] = PROXY_PREFIX + m
        kwargs["custom_llm_provider"] = "openai"
        kwargs["api_base"] = BASE
    else:
        kwargs["custom_llm_provider"] = "gemini"
    kwargs["api_key"] = KEY
    if who == "user":
        # tau-bench paper: "temperature is 0.0 for agent and 1.0 for user".
        # tau-bench's code sends no temperature for the user, so I set it here.
        kwargs["temperature"] = USER_TEMPERATURE
    kwargs.setdefault("num_retries", NUM_RETRIES)
    kwargs["timeout"] = CALL_TIMEOUT
    t0 = time.time()
    res = _real(*args, **kwargs)
    u = res.usage
    details = getattr(u, "completion_tokens_details", None)
    row = {"who": who, "sec": round(time.time() - t0, 2),
           "prompt": u.prompt_tokens, "completion": u.completion_tokens,
           "reasoning": getattr(details, "reasoning_tokens", None) or 0}
    with _lock, open(LOG, "a") as f:
        f.write(json.dumps(row) + "\n")
    # an empty reply crashes tau-bench's scoring; make it "" (a normal fail)
    msg = res.choices[0].message
    if msg.content is None and not msg.tool_calls:
        msg.content = ""
    return res


# patch BEFORE tau-bench imports completion, and patch the user module too
# (it keeps its own copy: the bug I hit in 06)
litellm.completion = logged
import tau_bench.envs.user as _user
_user.completion = lambda *a, **k: litellm.completion(*a, **k)

sys.argv[0] = "run.py"
from run import main
main()
