# E-reader Playground

My personal playground for repeatable setup across e-readers: KOReader on my
jailbroken Kindle, and personal CrossPoint firmware on my Xteink X4 Pro.
Experiments, scripts, plugin versions,
and optional device profiles live here; credentials and device state do not.

## KOReader setup

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
deliberately using the workflow below. By default existing settings remain
user-owned: `--userspace-proxy` only seeds missing settings. The separate
`--settings-profile` option below explicitly manages selected preferences.

Pass any mounted KOReader directory containing `plugins/`; nothing in the
installer hardcodes Kindle paths. For Kobo, this is typically the mounted
volume's `.adds/koreader` directory. On devices with separate KOReader data
and settings directories, configure settings through the device UI instead
of using either settings option. This script operates on locally accessible files,
not SSH or MTP destinations.

`--userspace-proxy` seeds the optional profile only when `settings/tailscale.lua`
does not exist. It forces userspace networking and automatically manages
KOReader's HTTP proxy. My Kindle PW5 needed this because kernel TUN behaved
incorrectly; omit the flag on devices that do not need it. This seed-only option
does not overwrite existing settings; enable those options in the plugin menu
or opt into the curated Kindle settings profile if needed.
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

## Shared fonts: Bookerly and Literata

`scripts/fonts.py` manages the same four **static TTF** styles (regular, bold,
italic, bold italic) for both readers. `profiles/fonts.json` defines the set and
pins the official Literata 3.103 release by SHA-256. Requirements: Python 3.9+
and `curl` for the initial download; no font-conversion dependencies.

Prepare the host library once:

```sh
python3 scripts/fonts.py fetch-literata
python3 scripts/fonts.py import-bookerly /path/to/private/bookerly
```

The Bookerly directory must contain `Bookerly-Regular.ttf`, `Bookerly-Bold.ttf`,
`Bookerly-Italic.ttf`, and `Bookerly-BoldItalic.ttf`. Bookerly is proprietary:
provide your own lawfully obtained copy and confirm your license permits its use
on the intended device. Kindle system fonts may live under `/usr/java/lib/fonts`
(not the USB-visible storage); this tool does not extract them or download fonts
from unofficial mirrors. KOReader discovering a system copy does not populate
this portable library. Font binaries and import checksums stay in gitignored
`.local/fonts/`; do not commit or redistribute that private directory.

Literata's OFL license travels with its files. The official release supplies
static fonts, so neither device needs variable-font handling or conversion.
Preparation validates family/style metadata; installs recheck cached hashes.
An existing import is not silently replaced: move its cache folder aside if
you intentionally want to import a different version.

Install offline, with the reader stopped and storage mounted:

```sh
# KOReader directory, not Kindle storage root
python3 scripts/fonts.py install /Volumes/Kindle/koreader --device koreader --check
python3 scripts/fonts.py install /Volumes/Kindle/koreader --device koreader

# X4 Pro SD root — substitute its actual mount path
python3 scripts/fonts.py install /Volumes/X4PRO --device x4pro --check
python3 scripts/fonts.py install /Volumes/X4PRO --device x4pro

# Or include both families in the existing KOReader setup command
python3 scripts/install.py /Volumes/Kindle/koreader --fonts --check
python3 scripts/install.py /Volumes/Kindle/koreader --fonts
```

By default **both families and all four styles are required**: missing or corrupt
sources fail before font writes, rather than silently falling back. To try only
Literata before sourcing Bookerly, explicitly add `--family Literata` to the
standalone installer. This is a partial install, not the guaranteed full set.
Existing setup commands without `--fonts` retain their previous behavior.

KOReader gets `fonts/Bookerly/` and `fonts/Literata/`; CrossPoint gets
`.fonts/Bookerly/` and `.fonts/Literata/`. The pinned CrossPoint checkout supports
direct TTF loading on the PSRAM-equipped X4 Pro; older firmware may need an
explicit build/flash first. This does **not** apply to the original X4/C3, which
needs converted `.cpfont` files. See
[CrossPoint's font documentation](crosspoint-reader/docs/sd-card-fonts.md).
The installer refuses `.cpfont` conflicts in its managed family folders because
CrossPoint gives those priority over TTFs. Its hidden `.fonts` families take
precedence over same-named families in the visible `fonts/` root.

`--check` is read-only: exit 0 means current, 1 means drift, 2 means an error.
Changed files are backed up under `.local/backups/fonts-*` before replacement;
unrelated fonts and reading preferences are preserved. Repeat installs are
no-ops. Each file replacement is atomic, but the whole install (and optional
plugin/settings setup) is not one transaction. To undo, stop the reader, restore
files from the printed backup, and remove newly added files marked
`"existed": false` in its `fonts.json` record.

Safely eject and restart. In KOReader, open an EPUB and select the family in the
font menu; in CrossPoint use **Settings > Reader > Font Family**. Test normal,
bold, italic, and bold-italic passages. Installation makes fonts available; it
does not change the selected family, existing per-book settings, or fonts baked
into PDFs. The curated KOReader profile still defaults to Bookerly.

## Curated KOReader preferences

The optional settings profiles capture my preferences without committing a
device's entire `settings.reader.lua`:

- `profiles/koreader/shared/`: typography, margins, footnotes/style tweaks,
  footer layout, cover screensaver, reading-statistics preferences, and BookOrbit
  catalog/sync preferences. Use `--fonts` after preparing the shared font library
  above to guarantee Bookerly and Literata are installed; no font is bundled here.
- `profiles/koreader/kindle/`: layers on the shared profile with touch gestures,
  frontlight actions, a 15-minute suspend timeout, `/mnt/us/Books` paths, and
  Tailscale userspace/automatic proxy settings. Bottom-left hold toggles the
  supported Tailscale VPN action; bottom-right hold in the reader syncs BookOrbit.

Requires **host LuaJIT**, in addition to Python/Git (`brew install luajit` on
macOS). Quit KOReader before applying changes so it cannot save over them.

```sh
# Preview preferences and launcher/code drift, without writing anything.
python3 scripts/install.py /Volumes/Kindle/koreader --kindle --settings-profile kindle --check
# Apply after reviewing the key names shown by the preview.
python3 scripts/install.py /Volumes/Kindle/koreader --kindle --settings-profile kindle
```

Use `--settings-profile shared` for another KOReader device with its settings
stored under the supplied KOReader directory. Profiles are explicit: neither
`--kindle` nor ordinary plugin installation opts you into preference management.
Add `--bookorbit` after fetching its pin if you also want its plugin code updated.
The Kindle settings profile replaces the need for the seed-only
`--userspace-proxy` flag: it manages those two Tailscale preferences even in an
existing settings file. It leaves the live global HTTP proxy to the plugin.

Profile files mirror their destinations and contain `set` tables (merge only
listed leaves), optional `seed` tables (fill only missing leaves), and optional
`remove` lists (explicit key paths). The only current removals are the obsolete
`toggle_tailscale_network` gesture actions. The shared profile seeds BookOrbit's
initial schema marker, preventing its first-run migration from discarding the
selected preferences; existing schema markers are preserved.

These are enforced preferences, not first-run suggestions: applying a profile
restores its selected values. Unlisted keys, nested settings, credentials,
device identity, reading history, location/warmth schedule, and per-book
overrides remain device-owned. BookOrbit's menu ordering is left to the plugin.
Settings files are parsed in a restricted Lua environment with an instruction
limit. Unsupported/corrupt data, symlinks, and a missing primary file with an
existing `.old` recovery file cause an error rather than a reset.

`--check` reports managed key names without printing values. An already-matching
profile creates no writes or backup. A real change backs up every affected
settings file locally, then replaces complete files atomically. Other values
are preserved, but formatting/comments in changed files are normalized. The
installer detects settings changed since planning; multiple files are not a
single atomic transaction, so keep KOReader stopped throughout.

Restore affected files from the printed backup path to undo the merge.
`settings-profile-files.json` in that backup records whether each changed file
previously existed; remove a newly created file instead of restoring it. Review
before restoring whole settings files, which also rolls back unrelated changes
made since the backup. Backups contain private device state and stay Git-ignored.

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

## BookOrbit plugin: public code, private device settings

[`profiles/bookorbit.json`](profiles/bookorbit.json) pins the **public** BookOrbit
release, exact source commit, and SHA-256 of the plugin files. The initial release
is **3.2.0**, matching the declared Docker image version. This is not a live check
of the running deployment, and custom image/plugin overrides are not covered.

BookOrbit's Dockerfile copies `koreader-plugin/bookorbit.koplugin/` directly from
its source. Its public repository's manual installation instructions use that
same directory. We can therefore fetch the unconfigured plugin at the matching
public release without access to a deployment, Docker, or a private Compose repo.
Anyone can reproduce these plugin code files; each reader supplies their own
server address and credentials on the device.

```sh
# Fetch the committed revision and verify its checksum; does not change the pin.
python3 scripts/bookorbit.py fetch
# Preview the combined Kindle setup without modifying the device.
python3 scripts/install.py /Volumes/Kindle/koreader --kindle --userspace-proxy --bookorbit --check
# Apply only after reviewing the proposed changes.
python3 scripts/install.py /Volumes/Kindle/koreader --kindle --userspace-proxy --bookorbit
```

Omit `--kindle` for another KOReader device. Fetching requires Git and network
access on first use; verified cached files work offline. Installation and `--check`
never fetch implicitly. The public source snapshot and plugin cache live in
ignored `.local/bookorbit/`; plugin code is not vendored or added as a submodule.
The checksum covers sorted relative filenames and their file-content hashes,
not tar metadata. Modified caches and credential-provisioning files are rejected.

The installer backs up the plugin, `settings.reader.lua`, and
`settings/bookorbit_sync_state.lua` on the computer before changes. Existing
login, sync state, and unrelated plugin files remain untouched. Preferences
also remain untouched unless `--settings-profile` is explicitly selected. This
is a code overlay, not a directory mirror: unrecognized/obsolete files are not
deleted or reported as drift. As with the other plugins, a matching install
causes no writes or new backup. Restore those backup paths to roll back; a first
install can be undone by removing only the newly added plugin directory.

**Do not commit or import a "preconfigured plugin" download.** BookOrbit embeds
the server URL and credentials in `bookorbit_provision.lua`. This workflow never
downloads that ZIP and refuses installation if such a file is pending on the
device. Configure a new reader through Tools → BookOrbit; an existing reader
keeps its login. No server URLs, account names, secrets, or private-repo content
are needed in tracked configuration.

When you deliberately update your BookOrbit server, select the matching public
release here (replace the example version):

```sh
python3 scripts/bookorbit.py pin 3.2.0
git diff -- profiles/bookorbit.json
python3 scripts/install.py /Volumes/Kindle/koreader --bookorbit --check
```

`pin` resolves that release once, downloads its public source, computes the
checksum, and updates the lock for review. It does not change the device or
commit anything. Future installs use the exact commit, even if a release tag
moves. Avoid the plugin's self-updater if you want it to remain at this repo's
pin; otherwise the next check will report drift and installation restores the pin.

## English and French dictionaries (host downloads, USB installation)

`profiles/dictionaries.json` pins GCIDE and reader.dict's French Wiktionnaire
StarDict package by archive SHA-256. Download on the Mac over HTTPS with retries,
then copy over USB: KOReader's slow HTTP mirrors and Kindle Wi-Fi are not involved.
Requires Python 3, `curl`, and host LuaJIT for the dictionary preference merge.

```sh
python3 scripts/dictionaries.py fetch
# Quit KOReader and mount the Kindle before installing.
python3 scripts/dictionaries.py install /Volumes/Kindle/koreader --check
python3 scripts/dictionaries.py install /Volumes/Kindle/koreader
# Repeat to verify: exit 0=current, 1=drift, 2=error.
python3 scripts/dictionaries.py install /Volumes/Kindle/koreader --check
```

The usual setup command can include dictionaries too (fetch both plugin and
dictionary pins first). Dictionary installation has its own backup transaction:

```sh
python3 scripts/bookorbit.py fetch
python3 scripts/dictionaries.py fetch
python3 scripts/install.py /Volumes/Kindle/koreader --kindle --bookorbit --settings-profile kindle --dictionaries --check
python3 scripts/install.py /Volumes/Kindle/koreader --kindle --bookorbit --settings-profile kindle --dictionaries
```

Archives stay in ignored `.local/dictionaries/<sha256>/` and are verified on every
use. Install and check are offline; neither downloads anything. For an alternate
download route, put the exact archives in a folder with the filenames from the
lock (`gcide.tar.gz`, `wiktionnaire-fr.zip`), then import them with:

```sh
python3 scripts/dictionaries.py fetch --from-dir /path/to/downloads
```

The French upstream URL is rolling, **not an immutable release URL**. Keep the
verified cache or a separate archive copy for future reinstalls. If upstream
replaces it, a fresh download fails the checksum rather than silently upgrading.
To update deliberately, inspect the new archive's source, metadata and members,
then review changes to its snapshot, SHA-256 and member list in the lock. Do not
replace a checksum merely to silence an unexplained mismatch. No dictionary
payloads are committed or redistributed by this repo.

Installation manages only the pinned files in `data/dict/gcide/` and
`data/dict/wiktionnaire-fr/`. Existing WordNet and French–English dictionaries,
unrelated settings, and book-specific preferences survive. The installer adds
language metadata to the extracted `.ifo` files, sets dictionary ordering, and
seeds **English (ereader)** and **French (ereader)** presets. Select these in
KOReader's **Dictionary presets** menu to switch languages; this is not automatic
language detection. English prefers GCIDE before WordNet; French prefers
Wiktionnaire before the existing translation dictionary. The fallback names and
relative paths match this Kindle setup; adjust the profile for other layouts.
Use standalone `install --no-preferences` to install files without changing order
or presets (and without requiring LuaJIT).

Only changed files are replaced, atomically per file. Before any device writes,
existing changed files and settings are backed up under private `.local/backups/`;
`dictionary-files.json` records which files previously existed. Index `.oft`
caches are backed up and invalidated only when their corresponding index changes.
A repeated install makes no writes or backup. Multiple files and plugin/dictionary
transactions are not one atomic operation: after a failure, rerun or restore the
recorded files from backup, removing only files recorded as previously absent.
Safely eject and restart KOReader before testing lookups on the device.

## Xteink X4 Pro / personal CrossPoint

`crosspoint-reader/` is a pinned submodule of
[my fork](https://github.com/jfmyers9/crosspoint-reader), configured to track
`personal/develop` for deliberate updates. It is separate firmware, not a
KOReader plugin. Nested FreeInk SDK and MicroLink submodules are also pinned.
This workflow never merges upstream or drops my personal features.

### Prepare and build

The pinned revision includes fixes for the missing mapped-input edge methods
and duplicate EPUB render definitions found in `f175cda9`. The corrected source
builds successfully for `x4pro` with pioarduino 6.1.19; 24 focused host tests
pass. Hardware behavior remains unverified; no device has been flashed by
this setup.

```sh
git submodule update --init --recursive
# Python 3.12 or 3.13 recommended for the embedded tooling (fork CI uses 3.13).
python3.12 scripts/x4pro.py prepare
python3 scripts/x4pro.py build
python3 scripts/x4pro.py ports
```

`prepare` creates `.local/crosspoint-toolchain/` and installs the fork's
pioarduino Core 6.1.19 and CI support dependencies without modifying global
Python. Repeat runs reuse it. The first build downloads compiler/platform
packages and can take substantial time and disk space. PlatformIO also uses
its normal user-level cache and may bootstrap `~/.platformio/penv`. Add
`--dry-run` to any command to print it without
installing, building, or flashing. Preparation requires network access.

The helper always selects **`x4pro` (ESP32-S3)**, never the default ESP32-C3
X4 target. It delegates incremental builds to PlatformIO. It rejects tracked
source changes, untracked files, mismatched nested submodules, and `platformio.local.ini`
overrides rather than silently building a different configuration. Commit
personal firmware changes in the fork before using this setup workflow.
Source revisions are pinned; Python transitive dependencies are not fully
locked, so this is not a bit-for-bit reproducible toolchain.

### Flash deliberately

Back up the SD card, including hidden `.crosspoint/` settings and reading state,
to private storage outside Git first. Exit USB Drive mode, connect a data-capable
USB cable, and use `ports` to identify your X4 Pro's serial port. A mounted SD
volume is not the flashing target. Close serial monitors before uploading.

```sh
python3 scripts/x4pro.py flash --port /dev/cu.usbmodemXXXX --dry-run
python3 scripts/x4pro.py flash --port /dev/cu.usbmodemXXXX --confirm-flash
```

Flashing is explicit every time: there is no automatic port selection, erase
step, or claim that the connected device's firmware already matches. PlatformIO
builds then uploads; it can overwrite application, bootloader, and partition
data. The helper does not back up flash/NVS or guarantee rollback. Confirm the
device is an **X4 Pro**, not an X4 or another ESP32-S3 board. It cannot infer the
board identity just from a serial path. USB-locked devices need the fork's
[documented unlock procedure](crosspoint-reader/README.md#usb-locked-devices-xteink-unlocker);
do not experiment with erase/unlock commands.

Alternatively, use `.pio/build/x4pro/firmware.bin` under `crosspoint-reader/`
with the [CrossPoint web flasher's Custom .bin option](https://crosspointreader.com/#flash-tools),
selecting X4 Pro. Verify boot, touch/buttons, frontlight, sleep/wake, book
opening, USB Drive mode, and your personal features after flashing.

### Personal features and secrets

The branch's standard `x4pro` build includes its experimental on-demand
Tailscale/MicroLink transport and BookOrbit features. Follow the fork's
[X4 Pro Tailscale setup](crosspoint-reader/docs/x4pro-tailscale.md) for enrollment
and its limitations. Wi-Fi, service credentials, auth keys, SD state, and NVS
identity remain device-owned; this repo does not provision or commit them.
Do not copy the Kindle's Tailscale plugin, binaries, or identity to this device.
Avoid official OTA updates if you want to retain the personal firmware.

### Update the personal branch pin

```sh
git -C crosspoint-reader fetch origin personal/develop
git -C crosspoint-reader switch personal/develop
git -C crosspoint-reader merge --ff-only origin/personal/develop
git -C crosspoint-reader submodule update --init --recursive
python3 scripts/x4pro.py build
git add crosspoint-reader
# Review and commit the new gitlink; flashing remains a separate explicit step.
```

After a recursive clone the submodule may be detached; `switch personal/develop`
reattaches to your fork's branch. Normal setup uses the recorded gitlink and
does not fetch the latest branch tip. The helper follows the fork's
`platformio.ini` X4 Pro target, `docs/x4pro-tailscale.md`, and CI dependency setup;
review those if you update the pin.
