# Security policy

This is an early community project. Only the current release is maintained. It is intended for a single administrator managing their own accounts, not mutually untrusted tenants.

For a vulnerability, use this repository's **Security → Report a vulnerability** page when private reporting is available. If unavailable, open an issue containing only a request for a private contact channel; do not disclose exploit details, credentials or account data in a public issue.

Include the affected version, a minimal reproduction using dummy data, expected behavior and impact. Never attach live profile exports or private keys.

## Deployment boundaries

- Keep the gateway on loopback and expose it through HTTPS or an SSH tunnel. Preserve authentication on every route.
- The gateway can launch an IDE with terminal/file access. An authenticated administrator has access to the privileges of the service user. The default systemd installer runs as root; use an unprivileged service account where your environment permits it.
- Independent profile directories prevent accidental account-state mixing. They are not OS security sandboxes. Conversations are deliberately shared between profiles.
- Browser CSP, IP-header removal and WebRTC restrictions reduce direct browser traffic from the suite. They are not a VPN, a firewall for other apps or a guarantee against fingerprinting and account risk checks.
- Configured proxies are verified before startup. Environment variables and Chromium proxy flags do not guarantee every extension or child program honors the proxy; network-level isolation is needed for a strict server egress guarantee.
- The internal HTTPS backend may use a self-signed certificate on loopback. Use public HTTPS for the external gateway; do not disable certificate verification for outbound HTTPS proxies.
- Back up private data before upgrading. Store exports privately: they contain account credentials and shared conversations.

See [deployment](docs/deployment.md) and [privacy boundaries](docs/privacy.md).
