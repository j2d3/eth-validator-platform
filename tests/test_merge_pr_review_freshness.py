"""Executable regressions for merge-review freshness after force-push."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "hack" / "merge-pr.sh"
HEAD = "0123456789abcdef0123456789abcdef01234567"


FAKE_GH = r'''#!/usr/bin/env bash
set -euo pipefail

args="$*"
if [[ "$args" == "api user --jq .login" ]]; then
  printf 'j2d3\n'
elif [[ "$args" == pr\ view\ 1* ]]; then
  jq -n --arg head "$FAKE_HEAD" '{
    author:{login:"j2d3"}, commits:[{}], headRefName:"codex/test",
    headRefOid:$head, state:"OPEN", isDraft:false, mergeable:"MERGEABLE",
    mergeStateStatus:"CLEAN", reviewDecision:"APPROVED"
  }'
elif [[ "$args" == "api /repos/j2d3/eth-validator-platform/pulls/1/reviews" ]]; then
  jq -n --arg head "$FAKE_HEAD" --arg submitted "$FAKE_REVIEW_SUBMITTED" '[{
    id:1, user:{login:"5u6r054"}, state:"APPROVED",
    commit_id:$head, submitted_at:$submitted
  }]'
elif [[ "$args" == *"/issues/1/events?per_page=100"* ]]; then
  if [[ -n "${FAKE_FORCE_PUSH_AT:-}" ]]; then
    jq -n --arg head "$FAKE_HEAD" --arg pushed "$FAKE_FORCE_PUSH_AT" '[[{
      event:"head_ref_force_pushed", commit_id:$head, created_at:$pushed
    }]]'
  else
    printf '[[]]\n'
  fi
elif [[ "$args" == *"/commits/$FAKE_HEAD/check-runs?per_page=100"* ]]; then
  # Reaching this call proves review freshness passed. Stop before mutation.
  printf '[]\n'
else
  printf 'unexpected fake gh call: %s\n' "$args" >&2
  exit 97
fi
'''


class MergeReviewFreshnessTests(unittest.TestCase):
    def run_wrapper(self, *, review_submitted: str, force_push_at: str = "") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_bin = Path(temp_dir)
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(FAKE_GH, encoding="utf-8")
            fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IXUSR)

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "FAKE_HEAD": HEAD,
                    "FAKE_REVIEW_SUBMITTED": review_submitted,
                    "FAKE_FORCE_PUSH_AT": force_push_at,
                }
            )
            return subprocess.run(
                [str(WRAPPER), "1"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_rejects_review_submitted_before_force_push_even_when_commit_id_matches(self) -> None:
        result = self.run_wrapper(
            review_submitted="2026-08-03T21:43:01Z",
            force_push_at="2026-08-03T21:44:33Z",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("GitHub remapped stale review metadata", result.stderr)
        self.assertIn("before current head force-push", result.stderr)
        self.assertNotIn("no check-runs", result.stderr)

    def test_accepts_review_submitted_after_force_push(self) -> None:
        result = self.run_wrapper(
            review_submitted="2026-08-03T21:50:26Z",
            force_push_at="2026-08-03T21:44:33Z",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("no check-runs", result.stderr)
        self.assertNotIn("remapped stale review", result.stderr)

    def test_exact_head_review_without_force_push_keeps_existing_path(self) -> None:
        result = self.run_wrapper(review_submitted="2026-08-03T21:43:01Z")

        self.assertEqual(result.returncode, 1)
        self.assertIn("no check-runs", result.stderr)
        self.assertNotIn("remapped stale review", result.stderr)


if __name__ == "__main__":
    unittest.main()
