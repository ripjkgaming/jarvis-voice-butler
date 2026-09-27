package dev.jarvis.link

import android.service.voice.VoiceInteractionService

/**
 * System voice-interaction entry point: makes JarvisLink selectable under
 * Settings > Apps > Default apps > Digital assistant app.
 *
 * The OS binds this service (BIND_VOICE_INTERACTION) when the user picks
 * Jarvis as their assistant — including at boot — and routes the assistant
 * button / gesture to [JarvisSessionService]. All UI and bridge traffic
 * lives in the session; this class stays a thin bind point on purpose.
 */
class JarvisInteractionService : VoiceInteractionService() {

    override fun onReady() {
        super.onReady()
        // Bound by the system. Nothing to start: sessions are created
        // on demand by JarvisSessionService when the user summons us.
    }

    override fun onShutdown() {
        super.onShutdown()
    }

    override fun onLaunchVoiceAssistFromKeyguard() {
        // Keyguard launch disabled in v1 (see interaction_service.xml):
        // never start a session over the lock screen.
    }
}
