# Call Recorder

An Omarchy Shell plugin that records desktop audio and a selected microphone.

Plugin id: `illegalstudio.omarchy-call-recorder`.

Start it from `Capture > Record call audio`. While recording, the red icon in
the right side of the bar opens controls for recorded audio levels, microphone
and desktop output selection, pause, source muting, and stop.

The desktop capture follows system output changes unless a specific output is
selected. The panel warns when Chrome is playing on a different output.

Each recording creates a mixed MP3, separate lossless FLAC tracks for the
microphone and desktop, and a JSON session report with route and control events.
Files are saved in `~/Music/Recordings/`. The mixed MP3 can be uploaded directly
to Plaud Web.

## Development

Run the complete local validation with:

```bash
make check
```

This validates `manifest.json`, runs `omarchy plugin validate .`, compiles the
Python backend, and checks the release script syntax.

## Releasing

Start an interactive release from a clean branch that tracks `origin`:

```bash
make release
```

The command reads local and remote tags without fetching. It proposes the next
patch after the latest `vMAJOR.MINOR.PATCH` tag. If the repository has no such
tag, it proposes the version already stored in `manifest.json`. Press Enter to
accept the proposal, or enter a semantic version with or without the `v` prefix.

After confirmation, the command updates `manifest.json`, runs `make check`,
creates a version commit when needed, creates an annotated tag, and pushes the
branch and tag to `origin` atomically. The tag starts the GitHub workflow, which
verifies the version and creates a GitHub Release with generated notes.
