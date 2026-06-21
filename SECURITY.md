# Security Policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or include credentials, recipe data, configuration files, or Kubernetes Secrets in a report.

Use GitHub private vulnerability reporting when it is enabled for the repository. Otherwise, contact the repository owner privately with a minimal reproduction and the affected CronPot version or commit.

## Handling secrets

- Keep `cronpot.toml`, `.env` files, Kubernetes kubeconfigs, GitHub tokens, and recipe vaults out of this repository.
- Set GitHub sync credentials through `CRONPOT_GITHUB_TOKEN`; do not place a token in a repository URL or a command-line argument.
- Store Kubernetes credentials and deployment configuration in GitHub Actions Secrets or the target cluster's Secret management system.
