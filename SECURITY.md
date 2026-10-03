# Security policy

## Reporting a vulnerability
Please **do not open a public issue**. Use GitHub's private reporting:
**Security > Report a vulnerability** on this repository. You will get a reply as soon as possible.

## What counts
SkyDispatch runs on your own computer and can optionally serve a local web remote and a telemetry share port on
your network. Reports about authentication bypass on those, unsafe handling of downloaded updates, or anything that
lets another website or device on the network control the app are especially welcome.

## Supported versions
Only the latest release receives fixes. The in-app updater (Help > Check for updates) installs it.

## Verifying downloads
Installers are published only on this repository's Releases page. GitHub shows a SHA-256 for each asset; the
in-app updater checks it before running anything.
