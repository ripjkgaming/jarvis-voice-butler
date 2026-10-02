package dev.jarvis.link.logic

import dev.jarvis.link.net.Bridge
import dev.jarvis.link.net.BridgeConfig
import dev.jarvis.link.net.Caption
import dev.jarvis.link.net.CallToken
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout

enum class CallPhase { IDLE, JOINING, LIVE, ERROR }

/** The LiveKit room, behind a seam so the call state machine is testable. */
interface CallEngine {
    interface Listener {
        fun onSpeaking(speaking: Boolean)
        /** The room ended without us asking (network drop, agent kicked us, server closed). */
        fun onLost(reason: String?)
    }

    var listener: Listener?

    /** Connect and publish the mic. Throws on failure. */
    suspend fun connect(url: String, token: String)

    suspend fun setMicEnabled(enabled: Boolean)

    /** Leave and release. Safe to call repeatedly. */
    suspend fun disconnect()
}

/** Foreground-service hook: keeps the process entitled to the mic during a call. */
interface CallHost {
    fun onCallStarting()
    fun onCallEnded()
}

data class CallState(
    val phase: CallPhase = CallPhase.IDLE,
    val detail: String = "",
    val muted: Boolean = false,
    val agentSpeaking: Boolean = false,
    /** Transcript, oldest first (newest last). */
    val captions: List<Caption> = emptyList(),
    val guest: Boolean = false,
    val notice: String = "",
)

/** Voice tab: LiveKit call state machine + live captions. */
class CallModel(
    private val bridge: Bridge,
    private val config: () -> BridgeConfig,
    private val engine: CallEngine,
    private val host: CallHost,
    private val messenger: Messenger,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
    private val joinTimeoutMs: Long = JOIN_TIMEOUT_MS,
    private val captionPollMs: Long = CAPTION_POLL_MS,
) : Model<CallState>(CallState(), scope, io), CallEngine.Listener {

    private var joinJob: Job? = null
    private var poller: Job? = null
    /** Set while we are the ones tearing the room down. */
    private var hangingUp = false

    init { engine.listener = this }

    val active: Boolean get() = current.phase == CallPhase.JOINING || current.phase == CallPhase.LIVE

    fun join(room: String = "", dispatch: Boolean = true) {
        if (active) return
        val c = config()
        if (c.offline) { fail("Offline mode is on, so calls are unavailable."); return }
        c.problem()?.let { fail(it.message); return }
        hangingUp = false
        update { it.copy(phase = CallPhase.JOINING, detail = "Requesting a call token...", muted = false, guest = c.guest, notice = "") }
        host.onCallStarting()
        joinJob = launch {
            val token: CallToken = when (val r = io { attempt { retryOnce { bridge.token(room, dispatch) } } }) {
                is Outcome.Ok -> r.value
                is Outcome.Fail -> { fail(r.message); return@launch }
            }
            update { it.copy(detail = "Joining the room...") }
            try {
                withTimeout(joinTimeoutMs) { engine.connect(token.url, token.token) }
            } catch (e: TimeoutCancellationException) {
                safeDisconnect()
                fail("Joining timed out. The agent may not have started; try again.")
                return@launch
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                safeDisconnect()
                fail(e.message?.ifEmpty { null } ?: "Could not join the call.")
                return@launch
            }
            if (hangingUp) { safeDisconnect(); return@launch }
            update { it.copy(phase = CallPhase.LIVE, detail = "Live", notice = "") }
        }
    }

    /** Join the call already running on the laptop (`GET /room`, dispatch=false). */
    fun joinLaptop() {
        if (active) return
        update { it.copy(notice = "Checking the laptop...") }
        launch {
            when (val r = io { attempt { bridge.room() } }) {
                is Outcome.Fail -> update { it.copy(notice = r.message) }
                is Outcome.Ok -> {
                    val room = r.value.room
                    if (room == null) {
                        update {
                            it.copy(
                                notice = if (r.value.waking) "The laptop is waking up. Try again in a few seconds."
                                else "No live laptop call right now.",
                            )
                        }
                    } else {
                        join(room, dispatch = false)
                    }
                }
            }
        }
    }

    fun hangup() {
        hangingUp = true
        joinJob?.cancel()
        joinJob = null
        launch { safeDisconnect() }
        host.onCallEnded()
        update { it.copy(phase = CallPhase.IDLE, detail = "", agentSpeaking = false, muted = false, notice = "Call ended.") }
    }

    fun setMuted(muted: Boolean) {
        if (current.phase != CallPhase.LIVE) return
        update { it.copy(muted = muted) }
        launch {
            try {
                engine.setMicEnabled(!muted)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                update { it.copy(muted = !muted, notice = "Could not change the mic: ${e.message}") }
            }
        }
    }

    fun toggleMute() = setMuted(!current.muted)

    /** Wake the laptop agent (`POST /summon`). */
    fun summon() {
        update { it.copy(notice = "Summoning...") }
        launch {
            val msg = when (val r = io { messenger.summon() }) {
                is Outcome.Ok -> "Summoned."
                is Outcome.Fail -> r.message
            }
            update { it.copy(notice = msg) }
        }
    }

    // ── Captions ─────────────────────────────────────────────────

    fun refreshCaptions() {
        launch { pullCaptions() }
    }

    private suspend fun pullCaptions() {
        when (val r = io { attempt { bridge.captions(8) } }) {
            // The bridge returns a chronological tail: oldest first, newest last.
            is Outcome.Ok -> update { it.copy(captions = r.value) }
            is Outcome.Fail -> update { it.copy(notice = r.message) }
        }
    }

    /** Poll captions every [captionPollMs] while the screen is visible. */
    fun startPolling() {
        if (poller?.isActive == true) return
        poller = launch {
            while (true) {
                pullCaptions()
                delay(captionPollMs)
            }
        }
    }

    fun stopPolling() {
        poller?.cancel()
        poller = null
    }

    // ── Engine callbacks ─────────────────────────────────────────

    override fun onSpeaking(speaking: Boolean) {
        update { if (it.phase == CallPhase.LIVE) it.copy(agentSpeaking = speaking) else it }
    }

    override fun onLost(reason: String?) {
        if (hangingUp || !active) return
        launch { safeDisconnect() }
        host.onCallEnded()
        update {
            it.copy(
                phase = CallPhase.ERROR,
                detail = "Call lost" + (reason?.let { r -> ": $r" } ?: ""),
                agentSpeaking = false,
            )
        }
    }

    // ── Internals ────────────────────────────────────────────────

    private fun fail(msg: String) {
        host.onCallEnded()
        update { it.copy(phase = CallPhase.ERROR, detail = msg, agentSpeaking = false) }
    }

    private suspend fun safeDisconnect() {
        try { engine.disconnect() } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (_: Exception) { }
    }

    private fun <T> retryOnce(block: () -> T): T = try {
        block()
    } catch (e: dev.jarvis.link.net.BridgeException) {
        if (e.error.transient) block() else throw e
    }

    companion object {
        const val JOIN_TIMEOUT_MS = 30_000L
        const val CAPTION_POLL_MS = 3_000L
    }
}
