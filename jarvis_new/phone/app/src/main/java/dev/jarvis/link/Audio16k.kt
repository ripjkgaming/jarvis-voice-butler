package dev.jarvis.link

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.util.Base64

/** 16kHz mono PCM16 helpers shared by Talk + the mic uplink. */
object Audio16k {
    const val RATE = 16000

    class Recorder {
        private var record: AudioRecord? = null
        private val chunks = mutableListOf<ShortArray>()
        @Volatile var recording = false
            private set

        fun start(): Boolean {
            val minBuf = AudioRecord.getMinBufferSize(
                RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
            )
            if (minBuf <= 0) return false
            val rec = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuf * 4,
            )
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                rec.release()
                return false
            }
            chunks.clear()
            recording = true
            rec.startRecording()
            record = rec
            Thread {
                val buf = ShortArray(1024)
                while (recording) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n > 0) chunks.add(buf.copyOf(n))
                }
            }.also { it.isDaemon = true; it.start() }
            return true
        }

        fun stopToBase64(maxSeconds: Int = 30): String {
            recording = false
            val rec = record
            record = null
            try { rec?.stop() } catch (_: Exception) { }
            rec?.release()
            var total = 0
            for (c in chunks) total += c.size
            val cap = RATE * maxSeconds
            val pcm = ShortArray(minOf(total, cap))
            var o = 0
            for (c in chunks) {
                val n = minOf(c.size, pcm.size - o)
                if (n <= 0) break
                c.copyInto(pcm, o, 0, n)
                o += n
            }
            val bytes = ByteArray(o * 2)
            for (i in 0 until o) {
                bytes[i * 2] = (pcm[i].toInt() and 0xFF).toByte()
                bytes[i * 2 + 1] = ((pcm[i].toInt() shr 8) and 0xFF).toByte()
            }
            return Base64.encodeToString(bytes, Base64.NO_WRAP)
        }
    }

    fun playPcm16(base64Pcm: String, rate: Int) {
        val bytes = Base64.decode(base64Pcm, Base64.DEFAULT)
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
            Thread.sleep((bytes.size / 2 * 1000L / rate) + 500)
            track.stop()
            track.release()
        }.also { it.isDaemon = true; it.start() }
    }
}
