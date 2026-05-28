"""Module entrypoint for `python -m coco_pr_review`."""
from __future__ import annotations

from coco_pr_review.github_event import main


if __name__ == "__main__":
    raise SystemExit(main())