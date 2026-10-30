package dev.jarvis.link.device

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AudioEffect
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import dev.jarvis.link.logic.CallEngine
import dev.jarvis.link.logic.CloseCodes
import dev.jarvis.link.logic.CloseOutcome
import dev.jarvis.link.logic.DisconnectReason
import dev.jarvis.link.logic.JitterBuffer
import dev.jarvis.link.logic.TopicFrame
import dev.jarvis.link.logic.TopicFrames
import dev.jarvis.link.logic.VoiceFrames
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * [CallEngine] over one authenticated WebSocket to the bridge's /voice endpoint
 * (docs/voice-transport.md). Mic and agent audio are binary frames; topics are
 * JSON text frames. Thin like [LiveKitEngine]: call policy stays in CallModel.
 *
 * The call token is single use, so a dropped socket is reported as lost; a
 * retry needs a fresh token from the bridge.
 */
class WebSocketEngine(context: Context) : CallEngine {
    private val appCtx = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val audioManager = appCtx.getSystemService(AudioManager::class.java)
    private val http = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    private val jitter = JitterBuffer()

    override var listener: CallEngine.Listener? = null

    /** Every topic frame, after the built-in handling (captions, barge-in, jarvis-tasks, ...). */
    var frameListener: ((TopicFrame) -> Unit)? = null

    @Volatile private var link: Link? = null
    /** Set while we are the ones closing the socket: our own close is never reported as lost. */
    @Volatile private var closing = false
    @Volatile private var muted = false
    private var mic: MicPump? = null
    private var playback: Playback? = null
    private var focus: AudioFocusRequest? = null

    override suspend fun connect(url: String, token: String): Unit = withContext(Dispatchers.Main.immediate) {
        disconnect()
        closing = false
        muted = false
        if (!Audio16k.hasMicPermission(appCtx)) throw IllegalStateException("Microphone permission is needed for calls.")
        val attempt = Link()
        link = attempt
        attempt.ws = http.newWebSocket(Request.Builder().url(endpoint(url, token)).build(), attempt)
        try {
            attempt.ready.await()
            startAudio(checkNotNull(attempt.ws) { "Call socket missing" })
        } catch (e: Exception) {
            withContext(NonCancellable) { disconnect() }
            throw e
        }
    }

    override suspend fun setMicEnabled(enabled: Boolean) {
        withContext(Dispatchers.Main.immediate) { muted = !enabled }
    }

    override suspend fun setSpeakerEnabled(enabled: Boolean) {
        withContext(Dispatchers.Main.immediate) { applyRoute(enabled) }
    }

    private fun applyRoute(enabled: Boolean) {
        if (Build.VERSION.SDK_INT >= 31) {
            if (enabled) {
                val speaker = audioManager.availableCommunicationDevices
                    .firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                    ?: throw IllegalStateException("Speaker unavailable")
                check(audioManager.setCommunicationDevice(speaker)) { "Speaker unavailable" }
            } else {
                audioManager.clearCommunicationDevice()
            }
        } else {
            audioManager.isSpeakerphoneOn = enabled
        }
        listener?.onAudioRoute(routeName())
    }

    override suspend fun disconnect(): Unit = withContext(Dispatchers.Main.immediate) {
        closing = true
        val old = link
        link = null
        stopAudio()
        try { old?.ws?.close(CloseCodes.CLIENT_REQUESTED, "client requested close") } catch (_: Exception) { }
    }

    /** Typed text to the server (`lk.chat`). False when not connected or the text is rejected. */
    fun sendChat(text: String): Boolean {
        val frame = VoiceFrames.chatFrame(text) ?: return false
        return link?.ws?.send(frame) ?: false
    }

    private fun endpoint(url: String, token: String): String {
        val sep = if (url.contains('?')) '&' else '?'
        return "$url${sep}token=${URLEncoder.encode(token, "UTF-8")}"
    }

    private fun startAudio(ws: WebSocket) {
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        requestFocus()
        listener?.onAudioRoute(routeName())
        playback = Playback()
        mic = MicPump(ws)
    }

    private fun stopAudio() {
        mic?.stop()
        mic = null
        playback?.stop()
        playback = null
        jitter.flush()
        focus?.let { audioManager.abandonAudioFocusRequest(it) }
        focus = null
        audioManager.mode = AudioManager.MODE_NORMAL
        if (Build.VERSION.SDK_INT >= 31) audioManager.clearCommunicationDevice() else audioManager.isSpeakerphoneOn = false
    }

    private fun requestFocus() {
        val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .build()
        audioManager.requestAudioFocus(req)
        focus = req
    }

    private fun routeName(): String {
        if (Build.VERSION.SDK_INT >= 31) {
            return when (audioManager.communicationDevice?.type) {
                AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "Speakerphone"
                AudioDeviceInfo.TYPE_BUILTIN_EARPIECE -> "Earpiece"
                null -> "Automatic"
                else -> "Headset"
            }
        }
        return if (audioManager.isSpeakerphoneOn) "Speakerphone" else "Earpiece"
    }

    /** One WebSocket attempt. Callbacks from a superseded attempt are ignored. */
    private inner class Link : WebSocketListener() {
        var ws: WebSocket? = null
        /** Completes on `hello` (server accepted us); fails if the socket dies first. */
        val ready = CompletableDeferred<Unit>()
        private var ended = false

        override fun onMessage(webSocket: WebSocket, text: String) {
            scope.launch { if (link === this@Link) onTopic(webSocket, text) }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (link !== this) return
            val pcm = VoiceFrames.agentAudio(bytes.toByteArray()) ?: return
            jitter.push(pcm)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            // Answer a server-initiated close so OkHttp can finish the handshake.
            webSocket.close(code, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            finish(code, null)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            finish(null, t)
        }

        private fun onTopic(ws: WebSocket, text: String) {
            val frame = TopicFrames.parse(text) ?: return
            when (frame) {
                is TopicFrame.Hello -> {
                    ready.complete(Unit)
                    listener?.onAudioStatus("Remote audio connected")
                }
                is TopicFrame.State -> listener?.onSpeaking(frame.agent == "speaking")
                TopicFrame.Flush -> playback?.flush()
                TopicFrame.Ping -> ws.send(VoiceFrames.pongFrame())
                else -> Unit
            }
            frameListener?.invoke(frame)
        }

        /**
         * Ends this attempt. Before `hello` the failure is thrown from connect(),
         * not reported as lost; after it, a requested close is silent and any
         * other close is reported with the mapped reason.
         */
        private fun finish(code: Int?, cause: Throwable?) = scope.launch {
            if (ended) return@launch
            ended = true
            val outcome = if (code == null) CloseOutcome.Lost(DisconnectReason.LOST) else CloseCodes.outcome(code)
            val message = when (outcome) {
                CloseOutcome.Requested -> DisconnectReason.CLIENT_INITIATED.message
                is CloseOutcome.Terminal -> outcome.reason.message
                is CloseOutcome.Lost -> cause?.message ?: outcome.reason.message
            }
            val wasLive = ready.isCompleted
            if (!wasLive) ready.completeExceptionally(IOException(message))
            if (link !== this@Link) return@launch
            link = null
            stopAudio()
            if (!wasLive || closing) return@launch
            listener?.onLost(message)
        }
    }

    /** Mic capture: 1024-byte blocks (32 ms at 16 kHz) sent as kind 0x01 until stopped. */
    @Suppress("MissingPermission")
    private inner class MicPump(private val ws: WebSocket) {
        private val rec: AudioRecord
        private val effects = mutableListOf<AudioEffect>()
        @Volatile private var running = true

        init {
            val minBuf = AudioRecord.getMinBufferSize(VoiceFrames.MIC_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            check(minBuf > 0) { "Microphone unavailable" }
            rec = AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                VoiceFrames.MIC_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf, VoiceFrames.MIC_BLOCK_BYTES * 4),
            )
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                rec.release()
                error("Microphone unavailable")
            }
            val session = rec.audioSessionId
            if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(session)?.let { effects += it.apply { setEnabled(true) } }
            if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(session)?.let { effects += it.apply { setEnabled(true) } }
            if (AutomaticGainControl.isAvailable()) AutomaticGainControl.create(session)?.let { effects += it.apply { setEnabled(true) } }
            rec.startRecording()
            Thread {
                val block = ByteArray(VoiceFrames.MIC_BLOCK_BYTES)
                var fill = 0
                try {
                    while (running) {
                        val n = rec.read(block, fill, block.size - fill)
                        if (n < 0) break
                        if (n == 0) continue
                        fill += n
                        if (fill == block.size) {
                            if (!muted) ws.send(VoiceFrames.micFrame(block, 0, block.size).toByteString())
                            fill = 0
                        }
                    }
                } finally {
                    try { rec.release() } catch (_: Exception) { }
                    effects.forEach { try { it.release() } catch (_: Exception) { } }
                }
            }.also { it.isDaemon = true; it.name = "jarvis-mic"; it.start() }
        }

        /** Ends capture. The reader thread releases the record and effects. */
        fun stop() {
            running = false
            try { rec.stop() } catch (_: Exception) { }
        }
    }

    /** Agent audio playback (24 kHz PCM16) paced from the jitter buffer. */
    private inner class Playback {
        private val track: AudioTrack
        private val generation = AtomicInteger(0)
        @Volatile private var running = true

        init {
            val minBuf = AudioTrack.getMinBufferSize(VoiceFrames.AGENT_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
            track = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(VoiceFrames.AGENT_RATE)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build(),
                )
                .setBufferSizeInBytes(maxOf(minBuf, VoiceFrames.AGENT_RATE * 2 / 5))
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
            track.play()
            Thread {
                var pending = ByteArray(0)
                var off = 0
                var pendingGen = -1
                try {
                    while (running) {
                        val cur = generation.get()
                        if (off >= pending.size || pendingGen != cur) {
                            pending = jitter.read(PLAY_CHUNK_BYTES)
                            off = 0
                            pendingGen = cur
                        }
                        val n = track.write(pending, off, pending.size - off, AudioTrack.WRITE_NON_BLOCKING)
                        if (n < 0) break
                        if (n == 0) Thread.sleep(5) else off += n
                    }
                } catch (_: InterruptedException) {
                } finally {
                    try { track.release() } catch (_: Exception) { }
                }
            }.also { it.isDaemon = true; it.name = "jarvis-playback"; it.start() }
        }

        /** Drop queued agent audio now (`flush` topic, sent on interrupt). */
        fun flush() {
            generation.incrementAndGet()
            jitter.flush()
            try {
                track.pause()
                track.flush()
                track.play()
            } catch (_: Exception) { }
        }

        fun stop() {
            running = false
        }
    }

    private companion object {
        /** 20 ms of 24 kHz mono PCM16. */
        const val PLAY_CHUNK_BYTES = 960
    }
}
