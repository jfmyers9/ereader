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

For my Kindle, include the optional no-framework launcher:

```sh
# Preview drift without modifying the device or creating a backup.
python3 scripts/install.py /Volumes/Kindle/koreader --kindle --userspace-proxy --check
# Apply the same setup; safe to repeat.
python3 scripts/install.py /Volumes/Kindle/koreader --kindle --userspace-proxy
```

`--check` exits 0 when managed files match, 1 when changes are needed, and 2
on an error. Applying an already-current setup performs no device writes and
creates no backup. Changed files are repaired; unrelated files are not removed.
Shell launchers must have an executable bit; other permissions are not enforced
because mounted FAT storage does not preserve ordinary Unix permissions.

"Up to date" means matching the checked-out plugin revision and this repo's
managed files, not the latest online release. Installation never fetches, pulls,
upgrades KOReader, or downloads Tailscale binaries. Update the submodule
deliberately using the workflow below. Existing settings remain user-owned:
the profile flag seeds missing settings, not enforces or audits their values.

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

When changes are needed, the installer backs up the existing plugin, its settings,
and `settings.reader.lua` under `.local/backups/` before overlaying code.
With `--kindle`, it also backs up the existing no-framework scriptlet.
It preserves Tailscale binaries,
auth keys, identity/state, and other runtime files. Backups contain secrets:
they are ignored by Git and stored in private directories. Do not publish them.
On PocketBook, Tailscale's external `/mnt/ext1/tailscale` data is neither modified
nor backed up by this installer.

Safely eject and restart KOReader. For a first-time install, select
**Network → Tailscale VPN → Install/Update Tailscale**, then sign in. Existing
installations normally retain their binaries and identity. Verify a tailnet
service actually works; copying files alone does not test device networking.
Any gesture bound to the removed combined toggle must be rebound on the device.

## Kindle no-framework launcher

`kindle/documents/KOReader No Framework.sh` preserves my existing KMC scriptlet:

```sh
/var/local/kmc/bin/kpm launch koreader --framework_stop
```

`--kindle` installs it into the mounted Kindle's `documents/` directory, alongside
the normal launcher. It requires an existing KMC setup with scriptlet support
and KOReader registered with KPM. The installer checks for `kmc/`, but cannot
verify `/var/local/kmc/bin/kpm` or its registration through USB storage. It does
not install KMC, replace `KOReader.sh`, edit KUAL, or make this the default mode.
This option is Kindle-specific; omit it for other e-readers.

This uses KOReader's supported `--framework_stop` path, also exposed in its
bundled KUAL menu as **Start KOReader (no framework)**. The installed Kindle
startup script stops Amazon's GUI (`lab126_gui` on modern firmware), runs
KOReader, and restarts the GUI on normal exit. Less background activity and
memory pressure can plausibly improve responsiveness; this is not a benchmark
or a guarantee of better battery life. Normal mode already pauses some services.

Keep it as an optional choice when reading primarily in KOReader. Amazon's UI
is unavailable while stopped, switching back requires restarting it, and an
abnormal launcher failure can require a device restart. Check sleep/wake,
frontlight, Wi-Fi/Tailscale, USB storage, and return to the Kindle home screen
on your firmware. Stopping the GUI is not the same as turning off Wi-Fi or
all system services. The plain launcher remains available as a fallback.

Evidence: the mounted device's `koreader/koreader.sh` handles framework stop
and restart, and `extensions/koreader/menu.json` advertises the same option.
See also [KOReader's Kindle launcher source](https://github.com/koreader/koreader/blob/master/platform/kindle/koreader.sh).

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

For the optional Kindle launcher, restore
`kindle/documents/KOReader No Framework.sh` from the backup into the device's
`documents/` directory. If it did not exist before installation, remove only
that newly installed scriptlet. The backup's `installation.txt` records this.

## Checks

```sh
python3 -m unittest discover -s tests
```

Tests use temporary mock installations, never the mounted e-reader.
