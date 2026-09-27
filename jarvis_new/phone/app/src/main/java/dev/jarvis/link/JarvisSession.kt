package dev.jarvis.link

import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.view.View
import android.widget.Button
import android.widget.TextView
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Host for [JarvisSession]. Bound by the system through the
 * VoiceInteractionService when the user summons Jarvis.
 */
class JarvisSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle): VoiceInteractionSession =
        JarvisSession(this)
}

/**
 * One assistant invocation: a voice-plate overlay (orb + status +
 * transcript) that records a short utterance, sends it to the PC bridge
 * /talk endpoint, shows the reply and plays Jarvis's voice back.
 *
 * No UI framework beyond stock Views; all bridge traffic runs on a worker
 * thread and posts back via [ui]. Lifetimes: [onShow] arms a single
 * exchange, [onHide]/[onDestroy] tear the recorder down so the mic is
 * never held past the session.
 */
class JarvisSession(ctx: Context) : VoiceInteractionSession(ctx) {

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var orb: OrbView
    private lateinit var status: TextView
    private lateinit var transcript: TextView
    private lateinit var listenBtn: Button

    private val recorder = Audio16k.Recorder()
    private val exchangeOpen = AtomicBoolean(false)
    private var worker: Thread? = null

    override fun onCreateContentView(): View {
        val v = layoutInflater.inflate(R.layout.voice_session, null, false)
        orb = v.findViewById(R.id.session_orb)
        status = v.findViewById(R.id.session_status)
        transcript = v.findViewById(R.id.session_transcript)
        listenBtn = v.findViewById(R.id.session_listen)
        v.findViewById<Button>(R.id.session_close).setOnClickListener { hide() }
        listenBtn.setOnClickListener { if (exchangeOpen.compareAndSet(false, true)) runExchange() }
        return v
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        HotwordGate.sessionActive = true
        val prefs = Prefs(context)
        if (!prefs.configured) {
            setStatus("Link JarvisLink first", "Open the app → Link tab → host + token, then summon me again.")
            listenBtn.isEnabled = false
            return
        }
        listenBtn.isEnabled = true
        setStatus("At your service, Sir…", "Tap Listen, then speak.")
        orb.energy = 0.35f
        // Hands-free: start listening immediately so the assistant button
        // alone is enough. Tap Listen for follow-ups.
        if (exchangeOpen.compareAndSet(false, true)) runExchange()
    }

    override fun onHide() {
        super.onHide()
        HotwordGate.sessionActive = false
        stopExchange()
    }

    override fun onDestroy() {
        HotwordGate.sessionActive = false
        stopExchange()
        super.onDestroy()
    }

    private fun stopExchange() {
        exchangeOpen.set(false)
        try { recorder.stopToBase64(1) } catch (_: Exception) { }
        worker?.interrupt()
        worker = null
    }

    private fun setStatus(title: String, body: String) {
        ui.post {
            status.text = title
            transcript.text = body
        }
    }

    /** Record ~6s, POST /talk, show + speak the reply. Worker thread. */
    private fun runExchange() {
        worker = Thread {
            try {
                ui.post {
                    status.text = "Listening…"
                    transcript.text = "Speak now."
                    orb.energy = 0.8f
                    listenBtn.isEnabled = false
                }
                if (!recorder.start()) {
                    ui.post {
                        status.text = "Mic unavailable"
                        transcript.text = "The assistant mic could not start."
                        orb.energy = 0.35f
                    }
                    return@Thread
                }
                Thread.sleep(6000)
                val audio = recorder.stopToBase64()
                ui.post {
                    status.text = "Thinking…"
                    orb.energy = 0.5f
                }
                val reply = LinkApi(Prefs(context)).talk(audio)
                RemoteLauncher.handleAction(context, reply.action)
                val show = buildString {
                    append("You: ").append(reply.transcript.ifEmpty { "(nothing heard)" }).append("\n\n")
                    append("Jarvis: ").append(reply.reply.ifEmpty { reply.warning ?: "(no reply)" })
                }
                TtsManager.init(context)
                ui.post {
                    status.text = "Jarvis"
                    transcript.text = show
                    orb.energy = if (reply.audioB64.isNotEmpty()) 1.0f else 0.35f
                    listenBtn.isEnabled = true
                    if (reply.audioB64.isNotEmpty()) {
                        try {
                            Audio16k.playPcm16(reply.audioB64, reply.audioRate)
                        } catch (_: Exception) { }
                    } else {
                        TtsManager.speak(reply.reply.ifEmpty { reply.warning ?: "" })
                    }
                    // Dump raw reply audio for bugtests (see SessionAudioProbe).
                    SessionAudioProbe.offer(reply.audioB64, reply.audioRate)
                }
            } catch (e: InterruptedException) {
                // Session torn down mid-exchange; mic already released.
                Thread.currentThread().interrupt()
            } catch (e: Exception) {
                ui.post {
                    status.text = "Link error"
                    transcript.text = "Error: ${e.message}"
                    orb.energy = 0.35f
                    listenBtn.isEnabled = true
                }
            } finally {
                exchangeOpen.set(false)
            }
        }.also { it.isDaemon = true; it.start() }
    }
}

/**
 * Test hook: the latest session reply audio, observable without a device.
 * Production code offers into it; instrumentation/unit tests poll it.
 * Bounded to one slot; cleared on take.
 */
object SessionAudioProbe {
    @Volatile private var slot: Pair<String, Int>? = null

    fun offer(audioB64: String, rate: Int) {
        if (audioB64.isNotEmpty()) slot = audioB64 to rate
    }

    fun take(): Pair<String, Int>? {
        val v = slot
        slot = null
        return v
    }

    fun decodePcm16(): ShortArray? {
        // java.util.Base64 (not android.util) so plain JVM unit tests
        // can exercise this without Robolectric. minSdk 28 >= 26, safe.
        val (b64, _) = take() ?: return null
        val bytes = java.util.Base64.getDecoder().decode(b64)
        val out = ShortArray(bytes.size / 2)
        for (i in out.indices) {
            out[i] = ((bytes[i * 2].toInt() and 0xFF) or ((bytes[i * 2 + 1].toInt()) shl 8)).toShort()
        }
        return out
    }
}
