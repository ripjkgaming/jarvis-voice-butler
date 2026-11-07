# Google OAuth refresh reminder

Google issues a seven-day refresh token while this External OAuth app is in
Testing. The bridge's `google_auth_reminder` sidecar checks the personal token
file and, five days after it was last saved, notifies the owner and opens the
existing `scripts/google_auth.py --account personal` browser flow. This leaves
about two days before the token's expected expiry.

The existing reconnect flow validates the saved account identity and scope
list before replacing the token. The reminder does not store credentials or
request additional access. It uses Google's normal consent page; the owner
must approve it. If the flow is not approved, Jarvis retries once per day.
Reconnect attempts are not launched during a private phone session.

The reminder uses the token file's modification time. After a manual reconnect,
the newly saved token starts a fresh five-day interval. If the saved token file
is absent, the reminder stays idle. To disable automatic reminder and browser
launching, set `JARVIS_GOOGLE_AUTH_REMINDER=0` for the bridge. After the OAuth
app is moved out of Testing, disable the reminder if the seven-day Testing
expiry no longer applies.

The bridge and computer must be running for the reminder to fire. When the
computer is off, Jarvis checks again the next time the bridge starts.
