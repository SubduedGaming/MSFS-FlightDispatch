# Security policy

## Reporting a vulnerability
Please **do not open a public issue**. Use GitHub's private reporting:
**Security > Report a vulnerability** on this repository. You will get a reply as soon as possible.

## What counts
SkyDispatch is a server on your own Windows PC that an Android app talks to over your home network. It also keeps an
older browser page and an optional telemetry share port. Reports about authentication bypass on any of those, unsafe
handling of downloaded updates, or anything that lets another website or device on the network control the server are
especially welcome.

## How the phone connection is protected
- The connection is **plain HTTP**, not encrypted. Use it on your home network, or over a VPN you set up yourself.
  The Android app refuses to connect to anything but private (home-network) addresses.
- A phone pairs with a **one-time code** (valid 5 minutes, one use) shown on the PC as a QR code. Five wrong codes in a
  minute lock that address out for a minute.
- After pairing the phone holds a long random **device token**. The PC stores only a hash of it (`devices.json`).
  Removing the phone on the **Phones** tab, or `skydispatch-server --revoke-all`, stops that token working at once.
- Anyone who can read the phone's app data can use its token, and anyone on your network can try to guess pairing
  codes during the five minutes a code is shown, so do not leave a code showing.
- Hardware, folder and AI-server settings can only be changed on the PC; the phone sees a small safe subset.

## Supported versions
Only the latest release receives fixes. The in-app updater (Help > Check for updates) installs it.

## Verifying downloads
Installers are published only on this repository's Releases page. GitHub shows a SHA-256 for each asset; the
in-app updater checks it before running anything.
