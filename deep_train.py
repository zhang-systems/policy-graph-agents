# Train one LoRA adapter for deep KiL.
#   python deep_train.py --fold 0 --mode kil   (fine-tuning + violation penalty = deep KiL)
#   python deep_train.py --fold 0 --mode sft   (fine-tuning only = ablation)
# Loss for a good dialogue: normal next-token loss on the agent's turns.
# Loss for a bad write call: PENALTY * -log(1 - p), where p is the call's
# average token probability given the dialogue before it ("unlikelihood").
# So the model learns the good runs AND learns to avoid actions whose graph
# preconditions are not met.
import argparse
import json
import math
import os
import random
import time

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

import deep_data as D
from settings_deep import (BASE_MODEL, LORA_R, LORA_ALPHA, LORA_DROPOUT, LR, EPOCHS, GRAD_ACCUM,
                           PENALTY, MAX_TRAIN_LEN, SEED)

p = argparse.ArgumentParser()
p.add_argument("--fold", type=int, required=True)
p.add_argument("--mode", choices=["sft", "kil"], required=True)
p.add_argument("--data", default="deep/examples.jsonl")
p.add_argument("--out", default="deep/adapters")
p.add_argument("--limit", type=int, default=0, help="only this many examples (smoke test)")
args = p.parse_args()

random.seed(SEED)
torch.manual_seed(SEED)
name = f"{args.mode}_f{args.fold}"
out_dir = os.path.join(args.out, name)
tok = AutoTokenizer.from_pretrained(BASE_MODEL)


kinds = ("pos", "neg") if args.mode == "kil" else ("pos",)
raw = D.load(args.data, args.fold, kinds)
items, skipped = [], 0
for e in raw:
    enc = D.encode(tok, e["messages"], e["kind"] == "neg", MAX_TRAIN_LEN)
    if enc is None:
        skipped += 1
        continue
    items.append((e["kind"], *enc))
random.shuffle(items)
if args.limit:
    items = items[: args.limit]
n_pos = sum(k == "pos" for k, _, _ in items)
n_neg = len(items) - n_pos
print(f"{name}: {n_pos} positives, {n_neg} negatives, {skipped} skipped (too long or empty)", flush=True)

model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype=torch.bfloat16,
                                             attn_implementation="sdpa").cuda()
model.config.use_cache = False
model.gradient_checkpointing_enable()
model.enable_input_require_grads()
model = get_peft_model(model, LoraConfig(
    r=LORA_R, lora_alpha=LORA_ALPHA, lora_dropout=LORA_DROPOUT, task_type="CAUSAL_LM",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
model.print_trainable_parameters()
inner = model.base_model.model                 # Qwen3ForCausalLM with LoRA layers inside


def token_logprobs(ids, mask):
    """log p of every masked token. I run the LM head only on those positions,
    so a 16k-token dialogue does not need a 16k x 152k logits matrix."""
    x = torch.tensor([ids], device="cuda")
    m = torch.tensor(mask, device="cuda")
    tgt = torch.nonzero(m[1:]).squeeze(-1) + 1
    h = inner.model(input_ids=x).last_hidden_state[0]
    logits = inner.lm_head(h[tgt - 1]).float()
    return torch.log_softmax(logits, -1).gather(-1, x[0, tgt].unsqueeze(-1)).squeeze(-1)


opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=LR)
steps = math.ceil(len(items) * EPOCHS / GRAD_ACCUM)
sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / 10) * max(0.0, 1 - s / max(steps, 1)))
model.train()
log, t0, n = [], time.time(), 0
for epoch in range(EPOCHS):
    random.shuffle(items)
    for kind, ids, mask in items:
        lp = token_logprobs(ids, mask)
        if kind == "pos":
            loss = -lp.mean()
        else:
            p_call = torch.exp(lp.mean()).clamp(max=1 - 1e-4)
            loss = PENALTY * -torch.log1p(-p_call)
        (loss / GRAD_ACCUM).backward()
        log.append({"kind": kind, "loss": round(loss.item(), 4)})
        n += 1
        if n % GRAD_ACCUM == 0 or n == len(items) * EPOCHS:
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad()
        if n % 50 == 0:
            recent = log[-50:]
            pl = [r["loss"] for r in recent if r["kind"] == "pos"]
            nl = [r["loss"] for r in recent if r["kind"] == "neg"]
            print(f"  step {n}/{len(items) * EPOCHS}  pos loss {sum(pl) / max(len(pl), 1):.3f}"
                  f"  neg loss {sum(nl) / max(len(nl), 1):.3f}  {(time.time() - t0) / 60:.1f} min", flush=True)

os.makedirs(out_dir, exist_ok=True)
model.save_pretrained(out_dir)
json.dump({"fold": args.fold, "mode": args.mode, "positives": n_pos, "negatives": n_neg,
           "skipped": skipped, "minutes": round((time.time() - t0) / 60, 1), "steps": log},
          open(os.path.join(out_dir, "train_log.json"), "w"))
print(f"saved {out_dir} after {(time.time() - t0) / 60:.1f} min")
