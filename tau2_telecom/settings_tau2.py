# frozen settings for every tau2-bench arm (telecom stretch goal)
# kept the same as the airline Gemini arms (settings_gemini.py) wherever tau2 allows it
MODEL = "gemini/gemini-3.1-flash-lite"   # agent and user simulator, same model as 05G-08
PRICE_IN, PRICE_OUT = 0.25, 1.50         # USD per 1M tokens, Gemini price list; for cost estimates only
TEMPERATURE = 0.0                        # agent temperature (tau2 default too)
USER_TEMPERATURE = 1.0                   # user temperature: same as my airline arms (tau2 default is 0.0)
MAX_STEPS = 100                          # tau2 default is 200; telecom needs more than tau-bench's 30
SEED = 300                               # tau2 default
CONCURRENCY = 4                          # same as airline; does not change results
DOMAIN = "telecom"
SPLIT = "test"                           # official tau2 telecom test split: 40 tasks
N_TRIALS = 8
COMMIT = "da0835f6c67baef58bdda18f2a3b037dfb3a1754"   # tau2-bench version, pinned for reproducibility
