package dev.jarvis.link

import android.content.Context
import io.livekit.android.LiveKit
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.room.Room
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * LiveKit realtime voice call — Kotlin port of Flutter `VoiceCtrl`
 * join/hangup/setMuted. Token minting (POST /token) stays in the caller;
 * this owns the Room: connect, mic publish (remote audio auto-plays),
 * speaker activity, and teardown. Callbacks arrive on the main thread.
 */
class VoiceCallManager(appContext: Context) {
    interface Listener {
        fun onState(state: VoiceCall.CallState, detail: String)
        fun onSpeaking(speaking: Boolean)
    }

    private val appCtx = appContext.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    var listener: Listener? = null
    var state: VoiceCall.CallState = VoiceCall.CallState.IDLE
        private set
    var detail: String = ""
        private set
    var muted: Boolean = false
        private set

    val live: Boolean get() = state == VoiceCall.CallState.LIVE

    private var room: Room? = null
    private var eventJob: Job? = null
    private var idleJob: Job? = null

    private fun emit(s: VoiceCall.CallState, d: String = "") {
        state = s
        detail = d
        listener?.onState(s, d)
    }

    /** Join with a bridge-minted (url, token); publishes the mic. */
    fun join(url: String, token: String) {
        if (state == VoiceCall.CallState.JOINING || state == VoiceCall.CallState.LIVE) return
        emit(VoiceCall.CallState.JOINING, "joining room…")
        scope.launch {
            try {
                val r = LiveKit.create(appCtx)
                eventJob = launch { r.events.collect { onEvent(it) } }
                r.connect(url, token)
                r.localParticipant?.setMicrophoneEnabled(true)
                room = r
                muted = false
                emit(VoiceCall.CallState.LIVE, "")
            } catch (e: Exception) {
                teardown()
                emit(VoiceCall.CallState.ERROR, e.message?.ifEmpty { null } ?: "join failed")
            }
        }
    }

    fun setMuted(m: Boolean) {
        muted = m
        scope.launch {
            try {
                room?.localParticipant?.setMicrophoneEnabled(!m)
            } catch (_: Exception) { }
        }
    }

    fun hangup() {
        scope.launch { teardown() }
        emit(VoiceCall.CallState.IDLE, "")
    }

    private fun onEvent(event: RoomEvent) {
        when (event) {
            is RoomEvent.ActiveSpeakersChanged -> {
                val anyone = event.speakers.isNotEmpty()
                listener?.onSpeaking(anyone)
                idleJob?.cancel()
                idleJob = null
                if (anyone) {
                    idleJob = scope.launch {
                        delay(2000)
                        listener?.onSpeaking(false)
                    }
                }
            }
            is RoomEvent.Disconnected -> {
                scope.launch { teardown() }
                emit(VoiceCall.CallState.IDLE, "")
            }
            is RoomEvent.FailedToConnect -> {
                scope.launch { teardown() }
                emit(
                    VoiceCall.CallState.ERROR,
                    event.error?.message?.ifEmpty { null } ?: "connect failed",
                )
            }
            else -> Unit
        }
    }

    private suspend fun teardown() {
        eventJob?.cancel()
        eventJob = null
        idleJob?.cancel()
        idleJob = null
        try {
            room?.disconnect()
        } catch (_: Exception) { }
        try {
            room?.release()
        } catch (_: Exception) { }
        room = null
    }

    fun destroy() {
        scope.launch { teardown() }
        scope.cancel()
        listener = null
    }
}
