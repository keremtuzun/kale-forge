"""Did v9's eval failures come from the model, or from my token budget?

Six of twelve v9 completions failed to produce balanced JSON. Both the elevator and the
climber are among the largest assemblies in the corpus, and the intent budget was 700 tokens,
so the obvious suspect is truncation in the harness rather than a regression in the model.
Re-run only the failures with a generous budget and report whether the output ends cleanly.
"""
import json
import sys

sys.path.insert(0, "/private/tmp/evalrepo/apps/kale-demo")
sys.path.insert(0, "/private/tmp")

from eval_cad_adapter import MODEL, SYSTEM_CAD, _first_json, score_cad  # noqa: E402
from app.services.robot_spec import SYSTEM_DESIGN  # noqa: E402
from mlx_lm import generate, load  # noqa: E402
from mlx_lm.sample_utils import make_sampler  # noqa: E402

FAILED_INTENT = [
    "MK5i swerve on Krakens at R2 with a ground-to-feeder tunnel intake and a deep climb.",
    "27 inch REEFSCAPE robot, three-stage cascade elevator, wristed carriage arm, no shooter.",
]
FAILED_CAD = [
    ("elevator", "28x28 REEFSCAPE robot with a 3 stage belt-rigged cascade tower."),
    ("climber", "28 inch robot with dual telescoping winch hooks."),
]

model, tokenizer = load(MODEL, adapter_path="/private/tmp/adapters/v9")
sampler = make_sampler(temp=0.3)


def ask(system, user, limit):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
    return generate(model, tokenizer, prompt=prompt, max_tokens=limit,
                    sampler=sampler, verbose=False)


report = []
for prompt in FAILED_INTENT:
    out = ask(SYSTEM_DESIGN, f"Design an FRC robot for this request.\n\n{prompt}", 2000)
    report.append({
        "task": "intent", "prompt": prompt[:60],
        "chars": len(out), "ends_closed": out.rstrip().endswith("}"),
        "parses_at_2000": _first_json(out) is not None,
    })

for subsystem, prompt in FAILED_CAD:
    out = ask(SYSTEM_CAD,
              f"Give me the {subsystem} assembly for this robot at part level.\n\n"
              f"Robot:\n{prompt}", 2600)
    parsed = _first_json(out)
    score = score_cad(parsed)
    report.append({
        "task": f"cad:{subsystem}", "prompt": prompt[:60],
        "chars": len(out), "ends_closed": out.rstrip().endswith("}"),
        "parses_at_2600": parsed is not None,
        "features": score.get("feature_count"), "clean": score.get("clean"),
    })

print(json.dumps(report, indent=2))
