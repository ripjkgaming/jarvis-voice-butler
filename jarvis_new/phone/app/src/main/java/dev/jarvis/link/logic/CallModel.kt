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

enum class CallPhase { IDLE, JOINING, LIVE, RECONNECTING, ERROR }

/** The LiveKit room, behind a seam so the call state machine is testable. */
interface CallEngine {
    interface Listener {
        fun onSpeaking(speaking: Boolean)
        /** The room ended without us asking (network drop, agent kicked us, server closed). */
        fun onLost(reason: String?)
        fun onAudioRoute(route: String) {}
        fun onAudioStatus(status: String) {}
        fun onReconnecting() {}
        fun onReconnected() {}
    }

    var listener: Listener?

    /** Connect and publish the mic. Throws on failure. */
    suspend fun connect(url: String, token: String)

    suspend fun setSpeakerEnabled(enabled: Boolean) {}

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
    val speaker: Boolean = false,
    val audioRoute: String = "",
    val audioStatus: String = "Waiting for remote audio",
    val agentSpeaking: Boolean = false,
    /** Transcript, oldest first (newest last). */
    val captions: List<Caption> = emptyList(),
    val guest: Boolean = false,
    val notice: String = "",
    val purpose: String = "",
    val incomingCallId: String = "",
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
    private val wallClock: () -> Long = System::currentTimeMillis,
) : Model<CallState>(CallState(), scope, io), CallEngine.Listener {

    private var joinJob: Job? = null
    private var poller: Job? = null
    private val pollingOwners=mutableMapOf<Any,Long>()
    private var pollingInterval=0L
    private var laptopJob: Job? = null
    private var teardown: Job? = null
    private var generation = 0L
    private var preparedToken: CallToken? = null
    private var schoolGuard: Job? = null
    /** Set while we are the ones tearing the room down. */
    private var hangingUp = false

    init { engine.listener = this }

    val active: Boolean get() = current.phase == CallPhase.JOINING || current.phase == CallPhase.LIVE || current.phase == CallPhase.RECONNECTING

    /** Only a visible accept action may provide a server-claimed incoming token. */
    fun joinAccepted(token: CallToken, purpose: String = "", callId: String = "") {
        if (active) return
        preparedToken = token
        join()
        update { it.copy(purpose=purpose.take(300), incomingCallId=callId) }
        schoolGuard?.cancel()
        schoolGuard = launch {
            while (active) {
                if (IncomingPolicy.quiet(wallClock())) { hangup(); return@launch }
                delay(250)
            }
        }
    }

    fun join(room: String = "", dispatch: Boolean = true) {
        if (active) return
        val c = config()
        if (c.offline) { fail("Offline mode is on, so calls are unavailable."); return }
        c.problem()?.let { fail(it.message); return }
        val attemptId = ++generation
        hangingUp = false
        update { it.copy(incomingCallId = "", captions=emptyList(), phase = CallPhase.JOINING, detail = "Requesting a call token...", muted = false, speaker = false, audioRoute = "", audioStatus = "Waiting for remote audio", guest = c.guest, notice = "") }
        host.onCallStarting()
        joinJob = launch {
            teardown?.join()
            if (attemptId != generation) return@launch
            val token: CallToken = when (val r = io { attempt { preparedToken?.also { preparedToken = null } ?: if (room.isEmpty() && dispatch) (bridge.session(false) ?: bridge.token(room, dispatch)) else bridge.token(room, dispatch) } }) {
                is Outcome.Ok -> r.value
                is Outcome.Fail -> { fail(r.message); return@launch }
            }
            if (attemptId != generation) return@launch
            if (!token.url.startsWith("wss://") || token.token.isBlank()) {
                fail("The bridge returned an invalid secure call token.")
                return@launch
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
            if (hangingUp || attemptId != generation) { return@launch }
            update { it.copy(phase = CallPhase.LIVE, detail = "Live", notice = "") }
        }
    }

    /** Join the call already running on the laptop (`GET /room`, dispatch=false). */
    fun joinLaptop() {
        if (active) return
        update { it.copy(notice = "Checking the laptop...") }
        val requestId = ++generation
        laptopJob?.let { it.cancel() }
        laptopJob = launch {
            val negotiated = io { attempt { bridge.session(true) } }
            if (requestId != generation) return@launch
            when (negotiated) {
                is Outcome.Fail -> { update { it.copy(notice = negotiated.message) }; return@launch }
                is Outcome.Ok -> negotiated.value?.let {
                    preparedToken = it
                    join()
                    return@launch
                }
            }
            val result = io { attempt { bridge.room() } }
            if (requestId != generation) return@launch
            when (val r = result) {
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
        schoolGuard?.cancel(); schoolGuard=null
        hangingUp = true
        ++generation
        preparedToken = null
        laptopJob?.cancel(); laptopJob = null
        joinJob?.cancel()
        joinJob = null
        teardown = launch { safeDisconnect() }
        host.onCallEnded()
        update { it.copy(incomingCallId = "", phase = CallPhase.IDLE, detail = "", agentSpeaking = false, muted = false, notice = "Call ended.") }
    }

    fun setMuted(muted: Boolean) {
        if (current.phase != CallPhase.LIVE) return
        val callId = generation
        update { it.copy(muted = muted) }
        launch {
            try {
                engine.setMicEnabled(!muted)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                if (callId == generation && active) update { it.copy(muted = !muted, notice = "Could not change the mic: ${e.message}") }
            }
        }
    }

    fun toggleMute() = setMuted(!current.muted)

    fun toggleSpeaker() {
        if (!active) return
        val callId = generation
        val enabled = !current.speaker
        launch {
            try {
                engine.setSpeakerEnabled(enabled)
                if (callId == generation && active) update { it.copy(speaker = enabled) }
            } catch (e: kotlinx.coroutines.CancellationException) { throw e }
            catch (_: Exception) { if (callId == generation) update { it.copy(notice = "Could not change audio route") } }
        }
    }

    override fun onAudioRoute(route: String) { if (active) update { it.copy(audioRoute = route) } }
    override fun onAudioStatus(status: String) { if (active) update { it.copy(audioStatus = status) } }

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
            is Outcome.Ok -> update { it.copy(captions = CaptionTurns.coalesce(r.value)) }
            is Outcome.Fail -> update { it.copy(notice = r.message) }
        }
    }

    /** Poll captions every [captionPollMs] while the screen is visible. */
    fun startPolling(owner: Any = this, intervalMs: Long = captionPollMs) {
        if (pollingOwners[owner]==intervalMs) return
        pollingOwners[owner]=intervalMs.coerceAtLeast(800)
        restartPolling()
    }
    fun stopPolling(owner: Any = this) {
        pollingOwners.remove(owner)
        restartPolling()
    }
    private fun restartPolling() {
        val interval=pollingOwners.values.minOrNull() ?: 0L
        if (interval==pollingInterval && poller?.isActive==true) return
        poller?.cancel();poller=null;pollingInterval=interval
        if (interval==0L) return
        poller=launch { while(true) { pullCaptions();delay(interval) } }
    }

    // ── Engine callbacks ─────────────────────────────────────────

    override fun onSpeaking(speaking: Boolean) {
        update { if (it.phase == CallPhase.LIVE) it.copy(agentSpeaking = speaking) else it }
    }

    override fun onReconnecting() {
        if (current.phase == CallPhase.LIVE) update { it.copy(phase = CallPhase.RECONNECTING, detail = "Reconnecting…", agentSpeaking = false) }
    }

    override fun onReconnected() {
        if (current.phase == CallPhase.RECONNECTING) update { it.copy(phase = CallPhase.LIVE, detail = "Live") }
    }

    override fun onLost(reason: String?) {
        if (hangingUp || !active) return
        ++generation
        joinJob?.cancel(); joinJob = null
        teardown = launch { safeDisconnect() }
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

    companion object {
        const val JOIN_TIMEOUT_MS = 30_000L
        const val CAPTION_POLL_MS = 3_000L
    }
}
