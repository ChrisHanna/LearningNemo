"""Score the invoice gateway's guardrails against the labeled cases in evals/guardrails/cases.yml.

Offline (default, no model calls): scores the deterministic rails and benign cases.
Live (--live): also scores the model-backed input, topical, and output checks through
the APIM guardrail route. Set OPENAI_GUARDRAIL_BASE_URL and GUARDRAIL_EVAL_API_KEY.

Exits 1 when any scored case is misclassified.
"""

import argparse
import asyncio
import os
import sys

from task_agent.control import guardrail_eval


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--live', action='store_true', help='call the real guardrail model through APIM')
    parser.add_argument('--cases', default=guardrail_eval.CASES, help='labeled cases file')
    args = parser.parse_args()
    cases = guardrail_eval.load_cases(args.cases)
    if args.live:
        gateway = guardrail_eval.live_gateway(base_url=os.environ['OPENAI_GUARDRAIL_BASE_URL'],
                                              api_key=os.environ['GUARDRAIL_EVAL_API_KEY'])
    else:
        gateway = guardrail_eval.offline_gateway()
    report = asyncio.run(guardrail_eval.evaluate(gateway, cases, mode='live' if args.live else 'offline'))
    print(guardrail_eval.render(report))
    return 1 if report.mismatches else 0


if __name__ == '__main__':
    sys.exit(main())
