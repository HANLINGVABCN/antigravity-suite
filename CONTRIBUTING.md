# Contributing

Bug reports, documentation fixes and pull requests are welcome. Discuss large changes in an issue before implementing them.

## Development

Linux is the deployment target; Python 3.9 or newer is required. Use an isolated environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
bash -n install.sh uninstall.sh
node --check static/floating.js
node --check static/privacy.js
python tools/check_public_tree.py
```

Tests use local fake backends and disposable directories. They do not require a Google account, real proxy or Antigravity installation. Linux runs every test; Windows skips tests requiring privileged filesystem symlinks.

Use meaningful regression tests for changes to account switching, shared history, proxy handling or the browser network boundary. Keep sample accounts and addresses fictional.

## Pull requests

Explain the problem, resulting behavior and relevant validation. Preserve existing account data and shared conversations when changing migrations. Describe any changes to external browser requests, authentication, persistent files or process supervision.

Never commit real credentials, OAuth application secrets, profile exports, chat transcripts, private keys, server addresses or unredacted screenshots. The public-tree check is an additional check; reviewers still need to inspect the diff.
