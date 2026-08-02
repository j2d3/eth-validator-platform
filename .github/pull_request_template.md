## Change

Describe the intended state change and why it is needed.

## Risk and validator safety

- [ ] No signing, withdrawal, mnemonic, keystore-password, or AWS credential material is present.
- [ ] I considered duplicate validator-client execution and slashing risk.
- [ ] I considered client sync, database compatibility, and rollback behavior.
- [ ] The change is testnet-only.

## Evidence

- [ ] `make check`
- [ ] Terraform plan reviewed when applicable
- [ ] Client release notes reviewed when applicable

## Rollback

Describe the Git revert or infrastructure recovery path. A rollback must never start a second validator client with the same keys.
