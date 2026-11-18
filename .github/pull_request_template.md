## What changes

## Why

## Deploy notes
- [ ] New env vars are added to `services/<svc>/env-contract.yaml` and to the values for **every** environment
- [ ] New secrets are sealed (`make seal-secrets`), never committed in plain text
- [ ] Backwards compatible with the version of the other service that is currently running
- [ ] Rollback plan: `helm rollback` / ArgoCD history, or revert this PR
