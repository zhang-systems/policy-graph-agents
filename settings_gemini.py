# frozen settings for every Gemini arm (baseline, validator, shallow, semi-deep)
# no GPU needed: the model runs on Google's side
BACKEND = "apikey"                  # "apikey": my Gemini API key (Colab Secret GEMINI_API_KEY)
                                    # "colab": Colab's built-in Gemini (small quota, ran out)
MODEL = "gemini-3.1-flash-lite"     # agent and user simulator, same model (cheap Gemini; 2.5-flash-lite is closed to new users)
PRICE_IN, PRICE_OUT = 0.25, 1.50    # USD per 1M tokens for MODEL, from the Gemini price list; for cost estimates only
PROXY_PREFIX = "google/"            # only for "colab": it wants "google/<model>"; tau-bench file names can't have "/"
TEMPERATURE = 0.0                   # agent temperature, same as the tau-bench paper
USER_TEMPERATURE = 1.0              # user simulator temperature, same as the tau-bench paper
CONCURRENCY = 4                     # dialogues at once; Gemini runs on Google's side, so this doesn't change results
NUM_RETRIES = 6                     # retry a call after a rate-limit error
CALL_TIMEOUT = 600                  # seconds per model call
ENV = "airline"
N_TASKS = 50
N_TRIALS = 8
