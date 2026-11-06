# Third-party software

The MIT license in this repository covers this suite's code and documentation. It does not grant rights to Google Antigravity or any third-party software.

- Google Antigravity is obtained separately from its official distribution. Its binaries, account credentials and proprietary application assets are not included here. Antigravity and Google are trademarks of their respective owners. This is an independent community project, not an official Google project.
- Python dependencies are installed from their package distributions and retain their own licenses: aiohttp, aiohttp-socks and their transitive dependencies.
- pytest and cryptography are development/test dependencies with their own licenses.
- systemd, Supervisor, Xvfb, Nginx and Caddy remain separate third-party applications.
- GitHub Actions in this repository use the official `actions/checkout` and `actions/setup-python` repositories.

Using this suite does not grant access to restricted Google OAuth scopes or change Antigravity's service terms, account eligibility or region availability.

OAuth registration compatibility: the suite reads application registration metadata from the user's installed official client at runtime. It distributes no client secret and does not include or relicense that application. This does not create new permissions or guarantee that Google accepts a particular authorization flow.
