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
