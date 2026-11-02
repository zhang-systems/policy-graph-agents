# Run one telecom arm with the normal tau2 CLI, after patching it for that arm.
#   python run_arm.py baseline  run --domain telecom ...
#   python run_arm.py validator run --domain telecom ...
#   python run_arm.py shallow   run --domain telecom ...
# baseline : tau2 as it ships (prose policy)
# validator: + runtime validator on the agent's write tools (validator_tau2.py)
# shallow  : + the policy graph as text rules after the prose policy (graph_text_tau2.py)
# Everything else (model, temperatures, tasks, seed) is the same command line.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ARM = sys.argv.pop(1)
assert ARM in ("baseline", "validator", "shallow"), ARM

from tau2.registry import registry  # noqa: E402

if ARM == "validator":
    import validator_tau2
    validator_tau2.install()
elif ARM == "shallow":
    import graph_text_tau2
    GRAPH = "\n<policy_graph>\n" + graph_text_tau2.full_text(graph_text_tau2.load()) + "\n</policy_graph>"
    _make = registry._domains["telecom"]

    def make_env(*args, **kwargs):
        env = _make(*args, **kwargs)
        env.policy = env.policy + GRAPH     # prose policy kept, graph text added after it
        return env

    registry._domains["telecom"] = make_env

print(f"[run_arm] arm = {ARM}", flush=True)
from tau2.cli import main  # noqa: E402
sys.argv[0] = "tau2"
main()
