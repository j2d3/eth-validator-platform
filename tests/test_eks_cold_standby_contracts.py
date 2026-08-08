"""Static contracts for the destructive-but-guarded EKS cold-standby operator."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "hack" / "eks-cold-standby.sh").read_text()
RUNBOOK = (ROOT / "docs" / "runbooks" / "eks-cold-standby.md").read_text()


class EksColdStandbyContracts(unittest.TestCase):
    def test_rds_protection_is_changed_before_destroy(self) -> None:
        """A destroy plan cannot apply the prerequisite in-place update."""
        prepare = SCRIPT.index("prepare_rds_for_destroy()")
        destroy = SCRIPT.index("down()")
        section = SCRIPT[prepare:destroy]
        self.assertIn("-target=aws_db_instance.web3signer", section)
        self.assertIn("rds_deletion_protection=false", section)
        self.assertIn("rds_final_snapshot_identifier", section)

    def test_load_balancer_cleanup_is_cluster_tag_scoped(self) -> None:
        section = SCRIPT[
            SCRIPT.index("delete_cluster_load_balancers()") : SCRIPT.index(
                "delete_detached_branch_enis_after_cluster_destroy()"
            )
        ]
        self.assertIn("kubernetes.io/cluster/", section)
        self.assertIn('.Value == "owned"', section)
        self.assertIn("delete-load-balancer", section)

    def test_branch_eni_cleanup_requires_detached_known_artifact(self) -> None:
        section = SCRIPT[SCRIPT.index("delete_detached_branch_enis_after_cluster_destroy()"):SCRIPT.index("down()")]
        self.assertIn('description,Values=aws-k8s-branch-eni', section)
        self.assertIn('[[ "$status" == "available"', section)
        self.assertIn("delete-network-interface", section)

    def test_destroy_plan_protects_durable_secrets_on_retry(self) -> None:
        self.assertGreaterEqual(SCRIPT.count('secret_deletes="$(tf show -json'), 2)
        self.assertGreaterEqual(SCRIPT.count('[[ "$secret_deletes" == "0" ]]'), 2)
        self.assertIn("retry destroy plan contains durable secret deletions", SCRIPT)

    def test_runbook_records_measured_recovery_and_cold_state(self) -> None:
        self.assertIn("First measured drill", RUNBOOK)
        self.assertIn("775s", RUNBOOK)
        self.assertIn("1,173 seconds", RUNBOOK)
        self.assertIn("seven durable secret", RUNBOOK)
        self.assertIn("encrypted restore snapshot", RUNBOOK)


if __name__ == "__main__":
    unittest.main()
