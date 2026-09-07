<p align="center">
  <img src="assets/logo-mark.svg" alt="Omarchy Call Recorder logo" width="130">
</p>

<h1 align="center">Omarchy Call Recorder</h1>

<p align="center">
  <em>Both sides of the call, safely on disk.</em>
</p>

<p align="center">
  <a href="https://github.com/illegalstudio/omarchy-call-recorder/stargazers"><img src="https://img.shields.io/github/stars/illegalstudio/omarchy-call-recorder?style=flat-square&logo=github&logoColor=white&label=stars&color=8B5CF6" alt="Stars"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/illegalstudio/omarchy-call-recorder?style=flat-square&color=8B5CF6" alt="License: MIT"></a>
  <a href="https://omarchy.org"><img src="https://img.shields.io/badge/Omarchy-4.x-8B5CF6?style=flat-square" alt="Omarchy 4.x"></a>
  <a href="https://x.com/nahime0"><img src="https://img.shields.io/badge/Follow-%40nahime0-8B5CF6?style=flat-square&logo=x&logoColor=white" alt="Follow @nahime0 on X"></a>
</p>

<p align="center">
  <strong>Microphone + desktop &middot; Live recorded levels &middot; Output-aware capture &middot; Separate lossless tracks</strong>
</p>

<p align="center">
  A native Omarchy Shell widget for recording calls with both sides intact. Capture a selected microphone and the desktop output into a ready-to-share MP3, monitor the levels that actually reach the recording, follow output changes, and keep lossless source tracks plus session metadata for recovery and diagnosis.
</p>

<p align="center">
  <a href="https://opensource.nahi.me"><strong>Official Website</strong></a>
</p>

---

Plugin id: `illegalstudio.omarchy-call-recorder`.

Start it from `Capture > Record call audio`. While recording, the red icon in
the right side of the bar opens controls for recorded audio levels, microphone
and desktop output selection, pause, source muting, and stop.

The desktop capture follows system output changes unless a specific output is
selected. The panel warns when Chrome is playing on a different output.

While recording, microphone and desktop audio are saved as separate lossless
FLAC tracks on a shared timeline. After stopping, the plugin mixes the completed
tracks into a 192 kbps MP3, without real-time mixing deadlines. The widget shows
export progress and notifies you when the MP3 is ready.

Files are saved in `~/Music/Recordings/`, together with a JSON session report
containing route changes, controls, timeline corrections, and export status.
The FLAC tracks remain available even if MP3 export fails. The mixed MP3 can be
uploaded directly to Plaud Web.

Requires Python with PyGObject, GStreamer (PulseAudio, FLAC, audio conversion,
resampling, audiorate, and level plugins), `pactl`, and `ffmpeg`/`ffprobe`.

## Development

Run the complete local validation with:

```bash
make check
```

This validates `manifest.json`, runs `omarchy plugin validate .`, and tests
capture timing, pause/mute controls, offline mixing, and export failures with
synthetic audio. Tests do not access the microphone or system playback.
