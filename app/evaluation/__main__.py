"""Allow ``python -m app.evaluation`` / ``python -m app.evaluation.runner``."""

from .runner import main

if __name__ == "__main__":
    raise SystemExit(main())
