package dev.jarvis.link.svc

import android.app.NotificationManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.R
import dev.jarvis.link.device.AndroidBattery
import dev.jarvis.link.device.Audio16k
import dev.jarvis.link.logic.MicUplink
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import kotlin.concurrent.thread

/**
 * Foreground service: streams 16 kHz mono PCM to the PC mic-uplink server
 * for remote "hey Jarvis" detection, buzzes/opens the app on WAKE, and keeps
 * phone battery telemetry flowing while it runs. Reconnects with backoff.
 */
class LinkService : Service() {
    @Volatile private var running = false
    private var worker: Thread? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var batteryReceiver: BroadcastReceiver? = null
    @Volatile private var socket: Socket? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val graph = JarvisApp.graph(this)
        if (!startMicForeground("Connecting...")) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (!graph.prefs.micUplink || !Audio16k.hasMicPermission(this) || !graph.config().configured) {
            updateNotif(
                when {
                    !Audio16k.hasMicPermission(this) -> "Microphone permission missing"
                    !graph.config().configured -> "Set the PC address and token in Settings"
                    else -> "Mic uplink is off"
                },
            )
            stopSelf()
            return START_NOT_STICKY
        }
        if (worker?.isAlive != true) {
            running = true
            acquireWakeLock()
            graph.telemetry.acquire()
            registerBattery()
            worker = thread(name = "mic-uplink", isDaemon = true) { loop() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        try { socket?.close() } catch (_: Exception) { }
        worker?.interrupt()
        try { batteryReceiver?.let { unregisterReceiver(it) } } catch (_: Exception) { }
        batteryReceiver = null
        if (worker != null) JarvisApp.graph(this).telemetry.release()
        try { wakeLock?.let { if (it.isHeld) it.release() } } catch (_: Exception) { }
        super.onDestroy()
    }

    private fun startMicForeground(text: String) =
        Notifs.startMicForeground(this, Notifs.ID_LINK, Notifs.ongoing(this, Notifs.CH_LINK, "JarvisLink listening", text))

    private fun updateNotif(text: String) {
        try {
            getSystemService(NotificationManager::class.java)
                .notify(Notifs.ID_LINK, Notifs.ongoing(this, Notifs.CH_LINK, "JarvisLink listening", text))
        } catch (_: Exception) { }
    }

    private fun acquireWakeLock() {
        val pm = getSystemService(PowerManager::class.java)
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "JarvisLink:mic").also {
            // Re-acquired on every (re)connect; never held longer than a connection cycle.
            it.acquire(15 * 60 * 1000L)
        }
    }

    private fun registerBattery() {
        val graph = JarvisApp.graph(this)
        batteryReceiver = object : BroadcastReceiver() {
            override fun onReceive(ctx: Context?, intent: Intent?) {
                AndroidBattery.sampleOf(intent)?.let { graph.telemetry.onBatteryChanged(it) }
            }
        }.also { registerReceiver(it, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) }
    }

    private fun loop() {
        var failures = 0
        while (running) {
            val streamed = try {
                stream()
            } catch (_: Exception) {
                false
            }
            if (!running) break
            failures = if (streamed) 1 else failures + 1
            updateNotif("Reconnecting (attempt $failures)...")
            try {
                Thread.sleep(MicUplink.backoff(failures))
            } catch (_: InterruptedException) {
                break
            }
            try { wakeLock?.acquire(15 * 60 * 1000L) } catch (_: Exception) { }
        }
    }

    /** One connection; true when the handshake succeeded (so backoff resets). */
    @android.annotation.SuppressLint("MissingPermission")
    private fun stream(): Boolean {
        val prefs = JarvisApp.graph(this).prefs
        val minBuf = AudioRecord.getMinBufferSize(
            MicUplink.RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION, MicUplink.RATE, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT, minBuf * 4,
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            updateNotif("Microphone is busy")
            return false
        }
        val sock = Socket()
        socket = sock
        var handshook = false
        try {
            sock.connect(InetSocketAddress(prefs.host, prefs.micPort), 10_000)
            sock.soTimeout = 35_000
            sock.tcpNoDelay = true
            val out = sock.getOutputStream()
            out.write(MicUplink.handshake())
            out.flush()
            val reader = BufferedReader(InputStreamReader(sock.getInputStream()))
            if (!MicUplink.ackOk(reader.readLine())) throw IllegalStateException("mic uplink refused")
            handshook = true
            updateNotif("Live: say \"hey Jarvis\"")
            sock.soTimeout = 0
            record.startRecording()
            thread(name = "wake-reader", isDaemon = true) { readWakeLines(reader) }
            val buf = ShortArray(1024)
            val bytes = ByteArray(2048)
            while (running && !sock.isClosed) {
                val n = record.read(buf, 0, buf.size)
                if (n < 0) break
                if (n == 0) continue
                out.write(bytes, 0, MicUplink.pcmBytes(buf, n, bytes))
            }
        } finally {
            try { record.stop() } catch (_: Exception) { }
            record.release()
            try { sock.close() } catch (_: Exception) { }
        }
        return handshook
    }

    private fun readWakeLines(reader: BufferedReader) {
        try {
            while (running) {
                val line = reader.readLine() ?: break
                if (MicUplink.isWake(line)) onWake()
            }
        } catch (_: Exception) { }
    }

    private fun onWake() {
        val n = NotificationCompat.Builder(this, Notifs.CH_WAKE)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("Yes, Sir?")
            .setContentText("Tap to talk")
            .setContentIntent(Notifs.openApp(this, ACTION_WAKE))
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        try { getSystemService(NotificationManager::class.java).notify(Notifs.ID_WAKE, n) } catch (_: Exception) { }
        try { WakeRouter.route(this) } catch (_: Exception) { }
    }

    companion object {
        const val ACTION_WAKE = "dev.jarvis.link.WAKE"
        const val ACTION_START_SERVICES = "dev.jarvis.link.START_SERVICES"
    }
}
