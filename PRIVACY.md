# Privacy Policy

`jpe` is a personal command-line assistant. This policy describes what it does
with your data.

## What is stored

- **OAuth tokens** for Google Calendar and Gmail, stored locally at
  `~/.local/state/jpe/google-token.json` with file permissions `0600`.
- **Memory notes** as markdown files under the configured memory directory,
  plus a local SQLite index.
- **Conversation history** in memory for the duration of a session.

## Where data goes

- **Google APIs** — only when you invoke a calendar or mail tool. Requests go
  directly from your machine to Google.
- **OpenRouter** — prompts and tool results are sent to the configured model
  provider to generate responses.
- **Nowhere else.** There is no analytics, no telemetry, no third-party
  service, and no server operated by this project.

## What is not collected

No account is created. No data is sold, shared, or transmitted to the author of
this software.

## Retention and deletion

All data is local. Delete `~/.local/state/jpe/` and the memory directory to
remove everything. Revoke Google access at
<https://myaccount.google.com/permissions>.

## Contact

Open an issue at <https://github.com/nespoli/jpe/issues>.
