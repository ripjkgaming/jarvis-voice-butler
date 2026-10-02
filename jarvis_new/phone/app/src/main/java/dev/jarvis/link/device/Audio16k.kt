package dev.jarvis.link.device

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import dev.jarvis.link.logic.AudioCapture
import dev.jarvis.link.logic.AudioPlayer
import java.io.ByteArrayOutputStream
import java.util.Base64

/** 16 kHz mono PCM16 capture and reply playback shared by PTT, the assistant and the mic uplink. */
object Audio16k {
    const val RATE = 16000

    fun hasMicPermission(ctx: Context): Boolean =
        ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    /** Records until [stop]; capped at [maxSeconds] of audio. */
    class Recorder(private val ctx: Context, private val maxSeconds: Int = 30) : AudioCapture {
        private var record: AudioRecord? = null
        private var reader: Thread? = null
        private val pcm = ByteArrayOutputStream()
        @Volatile private var recording = false

        @Synchronized
        override fun start(): Boolean {
            if (recording) return false
            if (!hasMicPermission(ctx)) return false
            val minBuf = AudioRecord.getMinBufferSize(
                RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
            )
            if (minBuf <= 0) return false
            val rec = try {
                @Suppress("MissingPermission")
                AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuf * 4,
                )
            } catch (_: Exception) {
                return false
            }
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                rec.release()
                return false
            }
            pcm.reset()
            try {
                rec.startRecording()
            } catch (_: Exception) {
                rec.release()
                return false
            }
            record = rec
            recording = true
            val cap = RATE * 2 * maxSeconds
            reader = Thread {
                val buf = ByteArray(2048)
                while (recording) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n > 0) {
                        synchronized(pcm) {
                            val room = cap - pcm.size()
                            if (room > 0) pcm.write(buf, 0, minOf(n, room))
                        }
                    } else if (n < 0) break
                }
            }.also { it.isDaemon = true; it.start() }
            return true
        }

        @Synchronized
        override fun stop(): ByteArray {
            recording = false
            val rec = record
            record = null
            try { rec?.stop() } catch (_: Exception) { }
            try { reader?.join(500) } catch (_: InterruptedException) { }
            reader = null
            try { rec?.release() } catch (_: Exception) { }
            return synchronized(pcm) { pcm.toByteArray().also { pcm.reset() } }
        }
    }

    /** Plays /talk reply audio (16-bit mono PCM, base64) asynchronously. */
    object Player : AudioPlayer {
        override fun play(audioB64: String, rate: Int) {
            val bytes = Base64.getMimeDecoder().decode(audioB64)
            if (bytes.isEmpty()) return
            val track = AudioTrack.Builder()
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(rate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build()
                )
                .setBufferSizeInBytes(maxOf(bytes.size, 4096))
                .setTransferMode(AudioTrack.MODE_STATIC)
                .build()
            track.write(bytes, 0, bytes.size)
            track.play()
            Thread {
                try {
                    Thread.sleep((bytes.size / 2 * 1000L / rate) + 500)
                } catch (_: InterruptedException) { }
                try { track.stop() } catch (_: Exception) { }
                track.release()
            }.also { it.isDaemon = true; it.start() }
        }
    }
}
