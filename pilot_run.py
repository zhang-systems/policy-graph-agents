# tau-bench gives reward 0 for ANY error, rate limits too
# that makes the baseline look worse than it is
# Groq free tier only allows ~7000 input tokens a minute,
# and one call is ~4000+, so I wait the time Groq asks for, then retry
import re, sys, time, litellm

# tau-bench puts the user model name in the result file name
# a "/" in the name breaks the path, so I use a short name
litellm.model_alias_map = {"gpt-oss-120b": "openai/gpt-oss-120b"}

_real = litellm.completion

def patient_completion(*args, **kwargs):
    for attempt in range(30):
        try:
            return _real(*args, **kwargs)
        except litellm.RateLimitError as e:
            msg = str(e)
            # one call bigger than the per-minute limit: waiting won't help
            m = re.search(r"Limit (\d+), Used \d+, Requested (\d+)", msg)
            if m and int(m.group(2)) > int(m.group(1)):
                raise
            # wait the time Groq asks for, plus a little
            w = re.search(r"try again in ([\d.]+)s", msg)
            wait = float(w.group(1)) + 2 if w else 30
            print(f"  rate limit, waiting {wait:.0f}s")
            time.sleep(wait)
    raise RuntimeError("still rate limited after 30 tries")

# swap it in BEFORE tau-bench imports completion
litellm.completion = patient_completion

sys.argv[0] = "run.py"
from run import main
main()
