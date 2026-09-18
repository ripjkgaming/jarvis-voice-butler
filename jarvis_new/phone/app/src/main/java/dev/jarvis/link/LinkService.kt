package dev.jarvis.link

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import kotlin.concurrent.thread

/**
 * Foreground service: streams 16kHz mono PCM to the PC mic-uplink server
 * (JARVIS_MIC_PORT) for remote "hey Jarvis" detection, and raises a
 * full-attention notification on WAKE. Auto-reconnects with backoff.
 * Started on boot (BootReceiver) and from Settings. Runs only while
 * micUplink is enabled in Prefs.
 */
class LinkService : Service() {
    companion object {
        const val CH_STATUS = "jarvis_link_status"
        const val CH_WAKE = "jarvis_link_wake"
        const val NOTIF_STATUS = 1
        const val NOTIF_WAKE = 2
        const val ACTION_WAKE = "dev.jarvis.link.WAKE"
        private const val SAMPLE_RATE = 16000
        private val BACKOFFS = longArrayOf(2_000, 5_000, 15_000, 30_000, 60_000)
    }

    @Volatile private var running = false
    private var worker: Thread? = null
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_STATUS, "JarvisLink link", NotificationManager.IMPORTANCE_LOW)
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_WAKE, "Jarvis wake", NotificationManager.IMPORTANCE_HIGH)
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!Prefs(this).micUplink) {
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIF_STATUS, statusNotif("Connecting…"))
        if (worker?.isAlive != true) {
            running = true
            val pm = getSystemService(PowerManager::class.java)
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "JarvisLink:mic")
                .also { it.acquire(12 * 60 * 60 * 1000L) }
            worker = thread(name = "mic-uplink", isDaemon = true) { loop() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        try { wakeLock?.release() } catch (_: Exception) { }
        super.onDestroy()
    }

    private fun statusNotif(text: String): Notification {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CH_STATUS)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("JarvisLink listening")
            .setContentText(text)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

    private fun loop() {
        val prefs = Prefs(this)
        var failures = 0
        while (running) {
            try {
                stream(prefs)
                failures = 0
            } catch (_: Exception) {
                // fall through to backoff
            }
            if (!running) break
            failures++
            updateNotif("Reconnecting… (attempt $failures)")
            try {
                Thread.sleep(BACKOFFS[minOf(failures - 1, BACKOFFS.size - 1)])
            } catch (_: InterruptedException) { break }
        }
    }

    private fun updateNotif(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTIF_STATUS, statusNotif(text))
    }

    private fun stream(prefs: Prefs) {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT, minBuf * 4,
        )
        val sock = Socket()
        try {
            sock.connect(InetSocketAddress(prefs.host, prefs.micPort), 10_000)
            sock.soTimeout = 35_000
            val out = sock.getOutputStream()
            out.write(JSONObject().put("rate", 16000).put("channels", 1).toString().plus("\n").toByteArray())
            val ack = BufferedReader(InputStreamReader(sock.getInputStream())).readLine()
            if (JSONObject(ack).optBoolean("ok") != true) throw IllegalStateException("handshake: $ack")
            updateNotif("Live — say \"hey Jarvis\"")
            record.startRecording()
            // WAKE lines arrive on the same socket; read them on a side thread.
            thread(name = "wake-reader", isDaemon = true) { readWakeLines(sock) }
            val buf = ShortArray(1024)
            val bytes = ByteArray(2048)
            while (running && !sock.isClosed) {
                val n = record.read(buf, 0, buf.size)
                if (n <= 0) continue
                var o = 0
                for (i in 0 until n) {
                    bytes[o++] = (buf[i].toInt() and 0xFF).toByte()
                    bytes[o++] = ((buf[i].toInt() shr 8) and 0xFF).toByte()
                }
                try {
                    out.write(bytes, 0, o)
                } catch (_: Exception) { break }
            }
        } finally {
            try { record.stop() } catch (_: Exception) { }
            record.release()
            try { sock.close() } catch (_: Exception) { }
        }
    }

    private fun readWakeLines(sock: Socket) {
        try {
            val reader = BufferedReader(InputStreamReader(sock.getInputStream()))
            while (running) {
                val line = reader.readLine() ?: break
                if (line.startsWith("WAKE")) onWake()
            }
        } catch (_: Exception) { }
    }

    private fun onWake() {
        val open = PendingIntent.getActivity(
            this, 1,
            Intent(this, MainActivity::class.java).setAction(ACTION_WAKE)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notif = NotificationCompat.Builder(this, CH_WAKE)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Yes, Sir?")
            .setContentText("Tap to talk")
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        getSystemService(NotificationManager::class.java).notify(NOTIF_WAKE, notif)
        // Also bring the app forward when possible.
        val launch = Intent(this, MainActivity::class.java).setAction(ACTION_WAKE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        try {
            startActivity(launch)
        } catch (_: Exception) { }
        sendBroadcast(Intent(ACTION_WAKE))
    }
}

/** Autostart the mic uplink after boot (only if enabled in Settings). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        if (!Prefs(context).micUplink) return
        val svc = Intent(context, LinkService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(svc)
        } else {
            context.startService(svc)
        }
    }
}
