from __future__ import annotations

import copy
import unittest
from pathlib import Path

from tools import validate_catalog


class CatalogValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = validate_catalog.load_yaml_documents()
        self.validators = validate_catalog.load_validators()

    def document(self, kind: str, name: str | None = None) -> tuple[Path, dict]:
        for path, document in self.documents:
            if document["kind"] == kind and (
                name is None or document["metadata"]["name"] == name
            ):
                return path, copy.deepcopy(document)
        suffix = f" named {name}" if name is not None else ""
        self.fail(f"missing test fixture kind {kind}{suffix}")

    def test_repository_catalog_is_valid(self) -> None:
        self.assertEqual(validate_catalog.schema_errors(self.documents, self.validators), [])
        self.assertEqual(validate_catalog.relational_errors(self.documents), [])

    def test_signing_requires_every_schema_gate(self) -> None:
        path, assignment = self.document(
            "ValidatorAssignment", "assignment-synthetic-01"
        )
        assignment["spec"]["lifecycle"] = "active"
        assignment["spec"]["signingEnabled"] = True

        errors = validate_catalog.schema_errors([(path, assignment)], self.validators)

        self.assertTrue(any("nodePairRef" in error for error in errors))
        self.assertTrue(any("slashingProtectionConfirmed" in error for error in errors))
        self.assertTrue(any("doppelgangerProtectionConfirmed" in error for error in errors))

    def test_active_assignment_requires_a_concrete_node_pair(self) -> None:
        path, assignment = self.document(
            "ValidatorAssignment", "assignment-synthetic-01"
        )
        assignment["spec"]["lifecycle"] = "active"

        errors = validate_catalog.schema_errors([(path, assignment)], self.validators)

        self.assertTrue(any("nodePairRef" in error for error in errors))

    def test_synthetic_identity_cannot_enable_validator_duties(self) -> None:
        documents = copy.deepcopy(self.documents)
        for _, assignment in documents:
            if assignment.get("metadata", {}).get("name") == "assignment-synthetic-01":
                assignment["spec"]["lifecycle"] = "active"
                assignment["spec"]["signingEnabled"] = True
                assignment["spec"]["nodePairRef"] = "pair-synthetic-01"
                assignment["spec"]["safety"] = {
                    "slashingProtectionConfirmed": True,
                    "doppelgangerProtectionConfirmed": True,
                }

        errors = validate_catalog.relational_errors(documents)

        self.assertIn("ValidatorAssignment/assignment-synthetic-01: synthetic identity may not sign", errors)

    def test_stopping_assignment_still_owns_exclusivity(self) -> None:
        documents = copy.deepcopy(self.documents)
        assignment_path, assignment = self.document(
            "ValidatorAssignment", "assignment-synthetic-01"
        )
        for _, document in documents:
            if document.get("metadata", {}).get("name") == "assignment-synthetic-01":
                document["spec"]["lifecycle"] = "stopping"
        second = copy.deepcopy(assignment)
        second["metadata"]["name"] = "assignment-synthetic-02"
        second["spec"]["lifecycle"] = "activating"
        documents.append((assignment_path, second))

        errors = validate_catalog.relational_errors(documents)

        self.assertTrue(any("multiple live assignments" in error for error in errors))

    def test_live_node_pair_reference_is_exclusive(self) -> None:
        documents = copy.deepcopy(self.documents)
        assignment_path, assignment = self.document(
            "ValidatorAssignment", "assignment-synthetic-01"
        )
        for _, document in documents:
            if document.get("metadata", {}).get("name") == "assignment-synthetic-01":
                document["spec"].update(
                    {"lifecycle": "active", "nodePairRef": "pair-shared-by-mistake"}
                )
        second = copy.deepcopy(assignment)
        second["metadata"]["name"] = "assignment-synthetic-02"
        second["spec"].update(
            {
                "validatorRef": "validator-synthetic-02",
                "lifecycle": "active",
                "nodePairRef": "pair-shared-by-mistake",
            }
        )
        identity_path, identity = self.document(
            "ValidatorIdentity", "validator-synthetic-01"
        )
        identity["metadata"]["name"] = "validator-synthetic-02"
        documents.extend([(identity_path, identity), (assignment_path, second)])

        errors = validate_catalog.relational_errors(documents)

        self.assertIn(
            "NodePair/pair-shared-by-mistake: multiple live assignments: "
            "assignment-synthetic-01, assignment-synthetic-02",
            errors,
        )

    def test_customer_id_is_unique(self) -> None:
        documents = copy.deepcopy(self.documents)
        customer_path, customer = self.document("Customer")
        customer["metadata"]["name"] = "customer-synthetic-copy"
        documents.append((customer_path, customer))

        errors = validate_catalog.relational_errors(documents)

        self.assertTrue(any("customerId duplicates" in error for error in errors))

    def test_signing_requires_active_customer(self) -> None:
        documents = copy.deepcopy(self.documents)
        for _, document in documents:
            if document["kind"] == "Customer":
                document["spec"]["lifecycle"] = "suspended"
            elif document.get("metadata", {}).get("name") == "validator-synthetic-01":
                document["spec"].update(
                    {
                        "lifecycle": "registered",
                        "synthetic": False,
                        "publicKey": "0x" + "11" * 48,
                        "signingSecretRef": "local-secret://validator-keystore",
                    }
                )
            elif document.get("metadata", {}).get("name") == "assignment-synthetic-01":
                document["spec"].update(
                    {
                        "lifecycle": "active",
                        "signingEnabled": True,
                        "nodePairRef": "pair-validator-01",
                        "safety": {
                            "slashingProtectionConfirmed": True,
                            "doppelgangerProtectionConfirmed": True,
                        },
                    }
                )

        errors = validate_catalog.relational_errors(documents)

        self.assertIn(
            "ValidatorAssignment/assignment-synthetic-01: signing requires an active customer",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
