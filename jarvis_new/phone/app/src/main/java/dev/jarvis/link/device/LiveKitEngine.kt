package dev.jarvis.link.device

import android.content.Context
import dev.jarvis.link.logic.CallEngine
import io.livekit.android.LiveKit
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.room.Room
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** [CallEngine] over the LiveKit Android SDK. Thin on purpose: all policy is in CallModel. */
class LiveKitEngine(context: Context) : CallEngine {
    private val appCtx = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    override var listener: CallEngine.Listener? = null

    private var room: Room? = null
    private var events: Job? = null
    private var idle: Job? = null
    /** Disconnects we asked for must not be reported as "lost". */
    @Volatile private var closing = false

    override suspend fun connect(url: String, token: String): Unit = withContext(Dispatchers.Main.immediate) {
        closing = false
        val r = LiveKit.create(appCtx)
        room = r
        events = scope.launch { r.events.collect { onEvent(it) } }
        try {
            r.connect(url, token)
            r.localParticipant.setMicrophoneEnabled(true)
        } catch (e: Exception) {
            disconnect()
            throw e
        }
    }

    override suspend fun setMicEnabled(enabled: Boolean) {
        room?.localParticipant?.setMicrophoneEnabled(enabled)
    }

    override suspend fun disconnect(): Unit = withContext(Dispatchers.Main.immediate) {
        closing = true
        events?.cancel(); events = null
        idle?.cancel(); idle = null
        val r = room
        room = null
        try { r?.disconnect() } catch (_: Exception) { }
        try { r?.release() } catch (_: Exception) { }
    }

    private fun onEvent(event: RoomEvent) {
        when (event) {
            is RoomEvent.ActiveSpeakersChanged -> {
                val anyone = event.speakers.isNotEmpty()
                listener?.onSpeaking(anyone)
                idle?.cancel()
                idle = null
                if (anyone) {
                    idle = scope.launch {
                        delay(2000)
                        listener?.onSpeaking(false)
                    }
                }
            }
            is RoomEvent.Disconnected -> if (!closing) listener?.onLost(event.error?.message)
            is RoomEvent.FailedToConnect -> if (!closing) listener?.onLost(event.error.message)
            else -> Unit
        }
    }
}
