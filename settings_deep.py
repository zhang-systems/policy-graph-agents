# frozen settings for 09 deep KiL (LoRA on an open model)
# The user simulator stays the same as every Gemini arm (settings_gemini.py),
# so the only thing that changes between 09 arms is the agent.
BASE_MODEL = "Qwen/Qwen3-8B"        # 7-8B open model, as in the project proposal
SERVED_BASE = "qwen3-8b"            # name of the base model on the vLLM server
AGENT_THINKING = False              # training data has no thinking, so the agent does not think
AGENT_MAX_TOKENS = 4096             # cap on one agent reply
MAX_MODEL_LEN = 32768               # vLLM max model length
GPU_MEM = "0.85"                    # vLLM gpu-memory-utilization

# training data: Gemini dialogues from 05G-08 (same tasks, so I split by task)
SOURCE_DIRS = ["results_gemini_baseline", "results_gemini_validator",
               "results_gemini_shallow", "results_gemini_semideep"]
N_FOLDS = 5                         # fold of task t = t % 5; a task is never tested by a model trained on it
MAX_POS_PER_TASK = 8                # cap on good dialogues per task, so easy tasks don't dominate
MAX_TRAIN_LEN = 16384               # skip longer examples (tokens)

# LoRA
LORA_R = 16
LORA_ALPHA = 32
LORA_DROPOUT = 0.05
LR = 1e-4
EPOCHS = 1
GRAD_ACCUM = 8
PENALTY = 0.5                       # weight of the violation penalty (0 = plain fine-tuning)
SEED = 0
