# YouTube downloads

Say **“Download this YouTube video”**, or give Jarvis a YouTube video link.
The direct `download_youtube_video(url="")` tool uses the currently viewed
YouTube video in your live Brave window. If you switched away or closed the
tab, it uses the last foreground YouTube video that Jarvis observed.

The bridge's `youtube_history` observer reads Brave's existing local CDP
endpoint (`JARVIS_BRAVE_CDP_PORT`, default `9222`). It checks page focus or
matches a visible tab to the foreground browser window; it never takes the
first background tab. Only canonical YouTube video URLs, titles and viewed
times are saved, in `$JARVIS_HOME/youtube/recent.json` and a bounded 100-entry
`history.jsonl`. Search pages and other browsing are excluded. Files have
private permissions. Set `JARVIS_YOUTUBE_HISTORY=0` to disable passive history;
explicit YouTube links still work.

Downloads now run headlessly through local yt-dlp with its bundled JavaScript
challenge scripts, Node.js and ffmpeg. Jarvis requires at least 1080p, picks the
closest to 1080p, prefers H.264/AAC, and merges streams into MP4. Videos with
no 1080p+ stream fail instead of downloading lower quality.
There is no converter website or advertisement in the default flow.

YouTube rejected anonymous requests on this machine. The working flow uses
the existing local Brave session (`JARVIS_YOUTUBE_COOKIES_BROWSER=brave`).
Cookies are read in the isolated downloader process and used for matching
YouTube requests; they are never exported to a cookie file, sent to YT1s, or
included in progress events or Jarvis job logs. Set this option to `off` to
use anonymous requests, or another supported yt-dlp browser name to select
that browser's local session. When YouTube needs authentication, open YouTube
in Brave and sign in before retrying.

The original requested [YT1s converter](https://yt1s.com.co/en228/youtube-to-mp4/)
remains available with `JARVIS_YOUTUBE_BACKEND=yt1s`. It is optional because
its verification endpoint still returns HTTP 403 on this machine.

Extraction is shown as **preparing**. Only actual transferred bytes (or an
actual browser file-download event for the YT1s backend) change the job to
**downloading**, create the start notification and
trigger the in-call spoken update. The worker lives in the bridge, so leaving
the voice call does not cancel the transfer. Completion and failure produce
desktop notifications and activity-feed updates. Repeating the same request
while it runs reuses the job, and its current status is reported accurately.

Files are saved in your desktop's configured Downloads directory, or
`JARVIS_DOWNLOADS_DIR` when set. Existing files are preserved with numbered
filenames; incomplete transfers are removed and only valid MP4 files become
the final output. The direct downloader additionally verifies that the result
contains playable video and audio streams. At most two different video
downloads run concurrently. Preparation has a 90-second deadline, transfer
has a 30-minute deadline, and cancellation kills downloader/ffmpeg children
and removes staging data.

The existing `~/.local/bin/resolve-autoconvert.sh` service watches Downloads
for `close_write`/`moved_to` events and converts finished MP4s to DNxHR HQX MOV
with PCM audio for Resolve. Jarvis publishes the complete, validated file
exclusively, then closes a writable descriptor without changing any bytes to
produce the watch event. Existing MP4 and MOV names are both excluded when
choosing a filename. When the converter removes an MP4 and replaces it with
the same-named MOV, download-status reads follow the MOV filename and path.
The converter itself remains outside Jarvis; its settings are preserved.

On 2026-10-03, a real direct download of the public 19-second “Me at the zoo”
video succeeded on this machine using the local Brave session. The final MP4
was checked with ffprobe for both audio and video. Automated tests cover real
isolated worker processes, delayed transfer notifications, filename collisions,
corrupt files, missing audio, deadlines, cancellation and the original
headless website's two-stage download and verification failure behavior.
The complete Python regression run passed 3,084 tests with 13 optional tests
skipped. Both live voice-routing checks passed, and an authenticated request
through the restarted production bridge reached preparing, downloading and
done with H.264 video and AAC audio in the saved MP4.

The authenticated bridge endpoints are `POST /youtube/download` with an
optional `url`, and `GET /youtube/download/<job-id>` for progress. Local system
control must be enabled to start a download; guest requests are refused.
