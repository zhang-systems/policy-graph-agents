# One runner for every Qwen arm (05Q-08Q). It does two things before running deep_run.py unchanged:
#  1. Simulator seed (brief: "Run each task at least 8 times with different simulator seeds"):
#     every call to the user simulator (Gemini) gets seed = SIM_SEED, the trial number 1..8.
#     Agent calls (Qwen on vLLM) are not touched. Each seeded call is counted in SEED_LOG.
#     If SIM_SEED is empty (Gemini does not accept a seed), nothing is changed.
#  2. Shallow KiL (07Q) when QWEN_SHALLOW=1: the whole policy graph, as text rules
#     (graph_text.py), is added to the agent's system prompt after the prose wiki.md.
import os
import runpy
import sys
import threading

import litellm

SEED = int(os.environ["SIM_SEED"]) if os.environ.get("SIM_SEED") else None
SEED_LOG = os.environ.get("SEED_LOG")
_lock = threading.Lock()


def _to_user_sim(kw):
    model = str(kw.get("model", ""))
    return kw.get("custom_llm_provider") == "gemini" or "gemini" in model.lower()


def _seeded(fn):
    if SEED is None or getattr(fn, "_seeded", False):
        return fn

    def wrap(*args, **kw):
        if _to_user_sim(kw):
            kw.setdefault("seed", SEED)
            if SEED_LOG:
                with _lock, open(SEED_LOG, "a") as f:
                    f.write(f"{SEED}\n")
        return fn(*args, **kw)

    wrap._seeded = True
    return wrap


# patch litellm first, so modules that later do "from litellm import completion" get the seeded one
litellm.completion = _seeded(litellm.completion)
import tau_bench.envs.user as user_mod  # noqa: E402

user_mod.completion = _seeded(user_mod.completion)

if os.environ.get("QWEN_SHALLOW") == "1":
    import graph_text as gt
    import tau_bench.envs.airline.env as airline_env   # the env reads WIKI when it is built
    airline_env.WIKI = airline_env.WIKI + "\n\n" + gt.full_text(gt.load())

here = os.path.dirname(os.path.abspath(__file__))
sys.argv[0] = os.path.join(here, "deep_run.py")
runpy.run_path(os.path.join(here, "deep_run.py"), run_name="__main__")
