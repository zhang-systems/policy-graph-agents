# run tau-bench against my local vLLM server
# every setting comes from settings.py, so all arms share them
import sys
import litellm
from settings import AGENT_THINKING, AGENT_MAX_TOKENS, CALL_TIMEOUT, EMPTY_REPLY_IS_FAIL

# hide litellm's "Give Feedback" lines, they flood the output
litellm.suppress_debug_info = True

_real = litellm.completion

def set_thinking(*args, **kwargs):
    # the agent's calls carry tools, the user simulator's calls don't
    is_agent = bool(kwargs.get("tools"))
    think = AGENT_THINKING if is_agent else False
    # copy first, so I never change tau-bench's own dict
    extra = dict(kwargs.get("extra_body") or {})
    extra["chat_template_kwargs"] = {"enable_thinking": think}
    kwargs["extra_body"] = extra
    if is_agent:
        kwargs.setdefault("max_tokens", AGENT_MAX_TOKENS)
    kwargs["timeout"] = CALL_TIMEOUT
    res = _real(*args, **kwargs)
    # an empty reply (thinking only) crashes tau-bench's scoring
    # turn it into "" so it counts as a normal fail
    msg = res.choices[0].message
    if EMPTY_REPLY_IS_FAIL and msg.content is None and not msg.tool_calls:
        msg.content = ""
    return res

# swap it in BEFORE tau-bench imports completion
litellm.completion = set_thinking

sys.argv[0] = "run.py"
from run import main  # run.py is in the repo folder
main()
