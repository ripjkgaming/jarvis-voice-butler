package dev.jarvis.link.logic

import dev.jarvis.link.net.BatterySample
import dev.jarvis.link.net.BridgeConfig
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay

enum class Link { UNKNOWN, CHECKING, ONLINE, FAILED }

data class HomeState(
    val link: Link = Link.UNKNOWN,
    /** Human line: "Bridge 1.2, up 3h" or the failure reason. */
    val linkDetail: String = "",
    /** Set when host/token are missing or malformed. */
    val configProblem: String? = null,
    val guest: Boolean = false,
    val offline: Boolean = false,
    val busy: Boolean = false,
    val recording: Boolean = false,
    /** Status/error line for the last action. */
    val notice: String = "",
    val lastTranscript: String = "",
    val lastReply: String = "",
    val battery: BatterySample? = null,
    val messages: List<Message> = emptyList(),
)

/** Home: connection status, quick PC actions, prompt box and push-to-talk. */
class HomeModel(
    private val config: () -> BridgeConfig,
    private val actions: PcActions,
    private val messenger: Messenger,
    private val talk: TalkService,
    private val capture: AudioCapture,
    private val conversation: Conversation,
    private val battery: () -> BatterySample?,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
    private val talkWindowMs: Long = TALK_WINDOW_MS,
) : Model<HomeState>(HomeState(), scope, io) {

    private var talkTimer: Job? = null

    init {
        launch { conversation.messages.collect { m -> update { it.copy(messages = m.takeLast(20)) } } }
        syncConfig()
    }

    /** Re-read settings (guest/offline/config problems) and the battery. */
    fun syncConfig() {
        val c = config()
        update {
            it.copy(
                guest = c.guest,
                offline = c.offline,
                configProblem = c.problem()?.message,
                battery = battery(),
            )
        }
    }

    fun refreshLink() {
        syncConfig()
        val c = config()
        if (c.offline) {
            update { it.copy(link = Link.UNKNOWN, linkDetail = "Offline mode: bridge calls are held.") }
            return
        }
        c.problem()?.let { p ->
            update { it.copy(link = Link.FAILED, linkDetail = p.message) }
            return
        }
        update { it.copy(link = Link.CHECKING, linkDetail = "Checking ${c.endpoint}...") }
        launch {
            when (val r = io { actions.ping() }) {
                is Outcome.Ok -> update { it.copy(link = Link.ONLINE, linkDetail = r.value) }
                is Outcome.Fail -> update { it.copy(link = Link.FAILED, linkDetail = r.message) }
            }
        }
    }

    // ── Prompt box: instant route first, chat fallback ───────────

    fun send(text: String) {
        if (current.busy) return
        update { it.copy(busy = true, notice = "Sending...") }
        launch {
            when (val r = io { messenger.send(text, instant = true) }) {
                is Outcome.Ok -> update {
                    it.copy(
                        busy = false,
                        lastTranscript = text.trim(),
                        lastReply = r.value.text,
                        notice = when {
                            r.value.summoned -> "Voice call summoned; the answer comes spoken."
                            r.value.warning != null && r.value.text.isEmpty() -> r.value.warning
                            r.value.viaRoute -> "Instant command"
                            else -> "Done"
                        },
                    )
                }
                is Outcome.Fail -> update { it.copy(busy = false, notice = r.message) }
            }
        }
    }

    // ── Push-to-talk ─────────────────────────────────────────────

    /** Tap-to-talk: records until [stopTalk] or [talkWindowMs], then sends. */
    fun startTalk() {
        val c = config()
        if (current.recording || current.busy) return
        if (c.offline) { update { it.copy(notice = "Offline mode is on.") }; return }
        c.problem()?.let { p -> update { it.copy(notice = p.message) }; return }
        HotwordGate.pttHeld = true
        if (!capture.start()) {
            HotwordGate.pttHeld = false
            update { it.copy(notice = "Microphone unavailable. Grant the microphone permission and try again.") }
            return
        }
        update { it.copy(recording = true, notice = "Listening...") }
        talkTimer = launch {
            delay(talkWindowMs)
            stopTalk()
        }
    }

    fun stopTalk() {
        if (!current.recording) return
        talkTimer?.cancel()
        talkTimer = null
        val pcm = try { capture.stop() } catch (_: Exception) { ByteArray(0) }
        HotwordGate.pttHeld = false
        update { it.copy(recording = false, busy = true, notice = "Thinking...") }
        launch {
            when (val r = io { talk.exchange(pcm) }) {
                is Outcome.Ok -> update {
                    it.copy(
                        busy = false,
                        lastTranscript = r.value.transcript,
                        lastReply = r.value.display,
                        notice = r.value.warning ?: "Done",
                    )
                }
                is Outcome.Fail -> update { it.copy(busy = false, notice = r.message) }
            }
        }
    }

    /** Release the mic if the app goes away mid-recording. */
    fun abortTalk() {
        if (!current.recording) return
        talkTimer?.cancel()
        try { capture.stop() } catch (_: Exception) { }
        HotwordGate.pttHeld = false
        update { it.copy(recording = false, notice = "Recording cancelled.") }
    }

    // ── Quick PC actions ─────────────────────────────────────────

    fun lock() = act("Lock") { actions.lock() }
    fun blackout() = act("Screens off") { actions.blackout() }
    fun restoreScreens() = act("Screens on") { actions.restoreScreens() }
    fun unlock(biometricConfirmed: Boolean) = act("Unlock") { actions.unlock(biometricConfirmed) }

    private fun <T> act(label: String, call: () -> Outcome<T>) {
        update { it.copy(busy = true, notice = "$label...") }
        launch {
            val msg = when (val r = io { call() }) {
                is Outcome.Ok -> "$label: done"
                is Outcome.Fail -> "$label failed: ${r.message}"
            }
            update { it.copy(busy = false, notice = msg) }
        }
    }

    companion object {
        const val TALK_WINDOW_MS = 6000L
    }
}
