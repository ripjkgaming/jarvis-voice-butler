package dev.jarvis.link.device

import android.content.Context
import android.media.AudioManager
import android.media.AudioAttributes
import com.twilio.audioswitch.AudioDevice
import io.livekit.android.AudioType
import io.livekit.android.AudioOptions
import io.livekit.android.LiveKitOverrides
import io.livekit.android.ConnectOptions
import io.livekit.android.audio.AudioSwitchHandler
import io.livekit.android.room.track.RemoteAudioTrack
import dev.jarvis.link.logic.CallEngine
import io.livekit.android.LiveKit
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.room.Room
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
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
    private var audio: AudioSwitchHandler? = null

    override suspend fun setSpeakerEnabled(enabled: Boolean) = withContext(Dispatchers.Main.immediate) {
        val handler = checkNotNull(audio) { "No call audio" }
        handler.selectDevice(if (enabled) checkNotNull(handler.availableAudioDevices.firstOrNull { it is AudioDevice.Speakerphone }) { "Speaker unavailable" } else null)
    }
    private var events: Job? = null
    private var idle: Job? = null
    /** Disconnects we asked for must not be reported as "lost". */
    @Volatile private var closing = false

    override suspend fun connect(url: String, token: String): Unit = withContext(Dispatchers.Main.immediate) {
        disconnect()
        closing = false
        val handler = AudioSwitchHandler(appCtx).apply {
            manageAudioFocus = true
            audioMode = AudioManager.MODE_IN_COMMUNICATION
            audioAttributeUsageType = AudioAttributes.USAGE_VOICE_COMMUNICATION
            audioAttributeContentType = AudioAttributes.CONTENT_TYPE_SPEECH
            preferredDeviceList = listOf(AudioDevice.WiredHeadset::class.java, AudioDevice.BluetoothHeadset::class.java, AudioDevice.Earpiece::class.java, AudioDevice.Speakerphone::class.java)
            registerAudioDeviceChangeListener { _, selected ->
                listener?.onAudioRoute(selected?.name ?: "No audio device")
            }
        }
        audio = handler
        val r = LiveKit.create(appCtx, overrides = LiveKitOverrides(audioOptions = AudioOptions(audioOutputType = AudioType.CallAudioType(), audioHandler = handler)))
        room = r
        events = scope.launch { r.events.collect { onEvent(it) } }
        try {
            r.connect(url, token, ConnectOptions(autoSubscribe = true))
            r.localParticipant.setMicrophoneEnabled(true)
        } catch (e: Exception) {
            withContext(NonCancellable) { disconnect() }
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
        audio = null
    }

    private fun onEvent(event: RoomEvent) {
        when (event) {
            is RoomEvent.TrackSubscribed -> (event.track as? RemoteAudioTrack)?.let {
                it.enabled = true
                it.setVolume(1.0)
                listener?.onAudioStatus("Remote audio connected")
            }
            is RoomEvent.TrackSubscriptionFailed -> listener?.onAudioStatus("Remote audio subscription failed; reconnect the call")
            is RoomEvent.TrackUnsubscribed -> if (event.track is RemoteAudioTrack) listener?.onAudioStatus("Remote audio disconnected")
            is RoomEvent.ActiveSpeakersChanged -> {
                val anyone = event.speakers.any { it != room?.localParticipant }
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
            is RoomEvent.Reconnecting -> if (!closing) listener?.onReconnecting()
            is RoomEvent.Reconnected -> if (!closing) listener?.onReconnected()
            is RoomEvent.Disconnected -> if (!closing) listener?.onLost(event.error?.message)
            is RoomEvent.FailedToConnect -> if (!closing) listener?.onLost(event.error.message)
            else -> Unit
        }
    }
}
