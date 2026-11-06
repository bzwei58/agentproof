# Security and privacy

AgentProof is local-first. It does not send project files, prompts, command output, or
session transcripts to a server. Imported sessions are reduced to counts and raw messages
are not stored in `.agentproof/task.json`.

## Keep evidence private

The `.agentproof/` directory contains file snapshots, command logs, and reports. It is
added to the repository's local Git exclude during `init`, but it is still your responsibility
to avoid copying it into a commit, issue, or public artifact.

- Logs can contain secrets printed by commands.
- Snapshots exclude common secret filenames such as `.env`, private-key extensions, and
  `credentials.json`; this is not a secret scanner.
- Review summaries can contain proprietary requirements. Keep them outside public issues.
- Do not run `verify` with untrusted commands. It executes the command with your account's
  permissions in the repository root.

## Reporting a vulnerability

Do not open a public issue with credentials, private source, or exploitable details. Use
GitHub's private vulnerability reporting for `bzwei58/agentproof` when available. Otherwise
contact the repository owner privately and include a minimal reproduction, affected version,
platform, and impact.
