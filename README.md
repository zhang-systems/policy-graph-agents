# policy-graph-agents

**Does turning an LLM agent's text policy into a graph make it follow the rules more consistently?**

I test this on the airline domain of [τ-bench](https://github.com/sierra-research/tau-bench), a benchmark where an LLM customer-service agent talks to an LLM-simulated customer and calls tools (book, change, cancel flights) on a real database.

---

## Situation

A customer-service agent gets a long policy written in plain text (`wiki.md`): "do not cancel a trip that has already been flown", "get an explicit *yes* from the user before you change anything", and so on.

The policy is already in the agent's prompt, but the agent does not follow it every time. τ-bench measures this with **pass^k**: a task counts only if the agent solves it in *all* k tries. In my baseline, the same task often passes in one try and fails in the next under the exact same settings.

## Task

I wanted to answer three questions:

- **RQ1:** If the policy is written as a **graph** (structured knowledge) instead of prose, does the agent become more consistent?
- **RQ2:** Does it matter **how deeply** the graph is used: only in the prompt (*shallow*), checked step by step before each action (*semi-deep*), or trained into the model (*deep*)?
- **H1:** If this Knowledge-infused Learning (KiL) really helps, it should beat a simple **runtime validator**: the same rules written as code that blocks bad tool calls. If the validator is just as good, the rules belong in code, not in the model.

## Action

1. **Built the policy graph.** I converted all 26 bullets of `wiki.md` into a graph: 7 actions, 29 rules, 19 "ask the user first" items, 6 OR/AND gates. Every rule keeps its source id (G1, C3, ...), and I checked each one against the original text. I tagged each rule as *data* (checkable from the database, 24 rules) or *dialogue* (only visible in the conversation, 5 rules). The graph is stored as JSON and loaded into Neo4j (70 nodes, 70 relationships).
2. **Chose a model under a small budget.** I tried free options first (Groq API, Qwen3-8B, Qwen3-32B on Colab GPUs, free Gemini tiers) and recorded why each did not work. I settled on `gemini-3.1-flash-lite` through the paid Gemini API, after a 10-task pilot to check it and estimate cost.
3. **Ran every arm the same way.** Same agent, model, prompt, and settings; 50 tasks × 8 trials = 400 dialogues per arm. I report pass^k for k = 1..8 with 95% bootstrap confidence intervals and paired comparisons over the same tasks.
4. **Built the arms:**
   - **Baseline:** prose policy only.
   - **Runtime validator:** the 24 data rules as Python code. It checks every write tool call before it reaches the database and blocks it with the name of the broken rule.
   - **Shallow KiL:** the graph rendered as a structured rule list and added to the prompt.
   - **Semi-deep KiL:** before each write call, the rules for that one action are pulled from the graph and the agent must check them one by one.
5. **Tested the tools, not just the agent.** I ran the validator on τ-bench's own answer keys. This is how I found that some answer keys break the written policy (see Result).
6. **Used AI tools in the workflow.** I used an AI coding assistant to draft code and the first version of the graph JSON. I verified the output with tests (for example, replaying every answer key through the validator) and by reading logs. One bug, a user-simulator setting that differed between arms, was caught this way and fixed before any result was used.

## Result

| Arm | pass^1 | pass^8 | Policy violations executed |
|---|---|---|---|
| Baseline (prose policy) | 0.525 | 0.360 | 70 (in 58 of 400 dialogues) |
| Runtime validator | 0.573 | 0.380 | 0 (69 blocked) |
| Shallow KiL | 0.520 | 0.340 | 31 (in 28 of 400 dialogues) |
| Semi-deep KiL | *running* | | |

![pass^k curves](figures/passk_curves.png)

**What I found**

- **The baseline is not consistent.** It solves about half of the tasks once, but only about a third every time. 19 of 50 tasks pass in some trials and fail in others.
- **The validator stops every checkable violation** and raises pass^1 by about 10 points on the 42 clean tasks (+0.104, 95% CI [+0.033, +0.185]). But pass^8 barely moves (+0.048, not significant). Code can block a wrong action; it cannot make the agent do a missing one, and it cannot see dialogue rules.
- **Shallow KiL cuts violations by more than half** (70 → 31), but pass^k does not change. The agent breaks fewer rules, but it does not finish more tasks. On the clean tasks it is significantly below the validator at k = 1, 2, 4.
- **Side findings about the benchmark:**
  - 8 of the 50 airline answer keys (tasks 10, 16, 19, 26, 27, 34, 45, 46) break τ-bench's own written policy. For example, task 26's answer key cancels a trip that has already been flown. I report results on all 50 tasks and on the 42 clean tasks.
  - `update_reservation_flights` changes the flights but does not save a new cabin class.

---

## Repository layout

```
notebooks/
  04_Policy_Graph.ipynb          build the graph, load it into Neo4j, draw it
  05G_Gemini_Baseline.ipynb      baseline arm
  06G_Gemini_Validator.ipynb     runtime validator arm + answer-key test
  07_Gemini_Shallow_KiL.ipynb    shallow KiL arm
  08_Gemini_SemiDeep_KiL.ipynb   semi-deep KiL arm
airline_policy_graph.json        the policy graph (master copy)
settings_gemini.py               settings shared by every arm
gemini_run.py                    runs tau-bench with Gemini and logs every call
policy_checker.py                the 24 data rules as code
violations.py                    counts violations in saved dialogues
graph_text.py                    turns the graph into text rules
kil_run.py                       shallow / semi-deep arms
results_gemini_*/                saved dialogues for every arm (8 trials each)
figures/                         plots used in this README
```

## How to run

The notebooks run on Google Colab (CPU is enough; the model runs at Google).

```bash
pip install git+https://github.com/sierra-research/tau-bench
git clone https://github.com/zhang-systems/policy-graph-agents cmsc691-10-project2
```

The notebooks expect the project at `/content/drive/MyDrive/cmsc691-10-project2`. Clone it there, or change the path in the second cell of each notebook.

**Free checks (no API key, no cost):**
- `06G_Gemini_Validator.ipynb`, answer-key test: replays τ-bench's answer keys through the validator.
- The analysis cells of 05G–08 (pass^k curves, paired comparisons, violation counts) read the saved results in `results_gemini_*/`.

**Re-running an arm:** add `GEMINI_API_KEY` to Colab Secrets. One arm (400 dialogues) costs about $6–8 with `gemini-3.1-flash-lite`.

## Credits

- [τ-bench](https://github.com/sierra-research/tau-bench) by Yao et al. (2024), MIT License. This repo uses it as a dependency and does not change it.
- Built as a course project for CMSC 691 Knowledge-Infused Learning, UMBC, Fall 2026.
