# Contributing to HopBench

Thanks for your interest. HopBench grades frequency-hop schedules against a simulated attacker suite.

## Getting started

Python 3 with numpy (scipy is used by some tests when installed).

```
python -m hopbench list
python -m hopbench run --quick --out /tmp/hopbench-out
python -m hopbench score module:YourSchedule --quick     # see examples/custom_schedule.py
```

Use `--out` outside the repository for experiments so tracked `results/` are not overwritten by accident.

## Testing

```
python -m pytest tests -q
```

The browser test for `break_the_hop/index.html` needs Playwright with Chromium and is skipped otherwise.

## Pull requests

- Open an issue first for larger changes.
- Keep PRs focused and fill in the pull request template.
- Follow `.editorconfig` and match the style of surrounding code.

## Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
