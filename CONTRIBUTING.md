# Contributing

Thanks for taking a look.

## Ground rules

1. **Never invent an endpoint or a field.** Every claim about the FragDenStaat API must
   be verifiable — against the live site, against the OpenAPI schema at
   `https://fragdenstaat.de/api/v1/schema/`, or against a specific place in
   [okfde/froide](https://github.com/okfde/froide) /
   [okfde/fragdenstaat_de](https://github.com/okfde/fragdenstaat_de). Cite it in a
   comment or a docstring.
2. **Never send a write to fragdenstaat.de while developing or testing.** No POST, PUT,
   PATCH or DELETE, not even once, not even "just to see". Tests that touch the network
   are marked `live` and perform read-only GETs.
3. **Do not weaken the safety gates.** The seven rules in the README's *Safety model* are
   the point of this project. If you change one, the test that proves it has to change
   with it, and the reasoning belongs in the pull request.
4. **No secrets in the repository.** Not in code, not in examples, not in test fixtures,
   not in commit messages. `.gitignore` covers `tokens.json`, `config.json`, `.env`,
   `*.pem` and `*.key`; do not work around it.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -m "not live"
```

`python -m pytest -m "not live"` must be green before you open a pull request. Run
`python -m pytest -m live` too if your change touches the API layer.

## Adding a rule

Rules live in `src/fds_mcp/rules.py`. Offline rules (`R…`) get `@offline_rule("Rnn-name")`
and take the draft dict; live rules (`L…`) get `@live_rule("Lnn-name")` and additionally
take a client. Use the next free number, do not renumber existing rules, and add a test
that makes the rule fire on a deliberately broken draft.

Pick the severity honestly:

- `ERROR` — froide would reject this, or it would produce a wrong or harmful request.
- `WARN` — likely a mistake, but a human might have meant it.
- `INFO` — worth knowing.

Only `ERROR` blocks a submission.

## Style

- Python 3.10+, type hints on public functions.
- Comments explain *why*, especially where froide's behaviour is surprising.
- Keep the docstring of every MCP tool accurate: it is the only thing the model reads.

## Reporting a security issue

Open a normal issue if it concerns the safety gates. If you have found something that
could cause a request to be sent without a human's consent, please say so plainly in the
title so it gets read first.
