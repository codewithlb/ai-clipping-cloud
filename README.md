# AI Clipping Cloud + Live

Cloud-ready AI clipping MVP with Video Mode and Live Mode.

Video Mode:
- Paste a permitted YouTube/Twitch URL.
- Server downloads the video.
- Faster-Whisper transcribes it.
- Highlight windows are ranked.
- Top moments are rendered as 9:16 MP4 clips.

Live Mode:
- Paste a permitted live-stream URL.
- Server resolves the current playable stream URL.
- A rolling stream is monitored.
- Recent speech is transcribed and scored.
- Strong moments are rendered as vertical clips.

Use only content you are authorized to download and clip. This app does not bypass DRM, private streams, paywalls, or access controls.

For production live clipping, use a persistent worker/queue and object storage. This MVP needs cloud compute and temporary disk space.
