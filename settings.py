# frozen settings for every arm (baseline, validator, shallow, semi-deep, deep)
# taken from the 50-task run in 03. Change one, and every arm must be rerun.
MODEL = "Qwen/Qwen3-32B-AWQ"     # 4-bit, on vLLM
SERVED = "qwen3-32b"             # short name tau-bench uses, agent and user
MAX_LEN = 32768                  # vLLM max model length
GPU_MEM = "0.90"                 # vLLM gpu-memory-utilization
AGENT_THINKING = True            # agent thinks, user simulator does not
AGENT_MAX_TOKENS = 8192          # cap on one agent reply
CALL_TIMEOUT = 1800              # seconds per model call
EMPTY_REPLY_IS_FAIL = True       # empty agent reply -> "" -> normal fail
TEMPERATURE = 0.0                # agent temperature
CONCURRENCY = 4                  # same as 03; batching changes results, so keep it fixed
ENV = "airline"
N_TASKS = 50
N_TRIALS = 8
