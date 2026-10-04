# KOReader Playground

My personal playground for reproducible KOReader setup across e-readers,
starting with my jailbroken Kindle. Experiments, scripts, plugin versions,
and optional device profiles live here; credentials and device state do not.

## Setup

Requires Git and Python 3.9+. Install KOReader separately first, quit it before
changing files, and mount the device's storage on your computer.

```sh
git submodule update --init --recursive
python3 scripts/install.py /Volumes/Kindle/koreader --userspace-proxy
```

Pass any mounted KOReader directory containing `plugins/`; nothing in the
installer hardcodes Kindle paths. For Kobo, this is typically the mounted
volume's `.adds/koreader` directory. On devices with separate KOReader data
and settings directories, configure settings through the device UI instead
of using the profile flag. This script operates on locally accessible files,
not SSH or MTP destinations.

`--userspace-proxy` seeds the optional profile only when `settings/tailscale.lua`
does not exist. It forces userspace networking and automatically manages
KOReader's HTTP proxy. My Kindle PW5 needed this because kernel TUN behaved
incorrectly; omit the flag on devices that do not need it. Existing settings
are never overwritten; enable those options in the plugin menu if needed.
Wi-Fi remains independent; the old combined network-stack toggle is not installed.

The installer backs up the existing plugin, its settings, and `settings.reader.lua`
under `.local/backups/` before overlaying code. It preserves Tailscale binaries,
auth keys, identity/state, and other runtime files. Backups contain secrets:
they are ignored by Git and stored in private directories. Do not publish them.
On PocketBook, Tailscale's external `/mnt/ext1/tailscale` data is neither modified
nor backed up by this installer.

Safely eject and restart KOReader. For a first-time install, select
**Network → Tailscale VPN → Install/Update Tailscale**, then sign in. Existing
installations normally retain their binaries and identity. Verify a tailnet
service actually works; copying files alone does not test device networking.
Any gesture bound to the removed combined toggle must be rebound on the device.

## Plugin version

`koreader-tailscale/` is a submodule of
[my fork](https://github.com/jfmyers9/koreader-tailscale), based on
[upstream](https://github.com/victoria-riley-barnett/koreader-tailscale).
The parent repo's gitlink pins the plugin revision, rather than following a
moving branch. This does not yet pin KOReader itself or the downloaded Tailscale
binaries, nor reproduce every KOReader setting or other installed plugin.

To update deliberately (review changes before pushing or installing):

```sh
git -C koreader-tailscale fetch https://github.com/victoria-riley-barnett/koreader-tailscale.git main
git -C koreader-tailscale switch main
git -C koreader-tailscale merge --ff-only FETCH_HEAD
python3 -m unittest discover -s tests
git -C koreader-tailscale push origin main
git add koreader-tailscale
# Commit the updated pin with your other intended setup changes.
```

## Recovery

With KOReader stopped and storage mounted, locate the backup printed by the
installer. Restore its `plugins/tailscale.koplugin/` over the device plugin.
Restore `settings/tailscale.lua` if it was backed up; otherwise remove the
newly seeded file to return to the prior settings state. `installation.txt`
records the target and whether those settings existed. Restore the backed-up
`settings.reader.lua` only if you also want to roll back global reader settings.
Safely eject and restart KOReader. Restoring state may roll back changes made
since the backup.

## Checks

```sh
python3 -m unittest discover -s tests
```

Tests use temporary mock installations, never the mounted e-reader.
