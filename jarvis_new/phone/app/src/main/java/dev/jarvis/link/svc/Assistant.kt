package dev.jarvis.link.svc

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import android.view.View
import android.widget.Button
import android.widget.TextView
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.R
import dev.jarvis.link.device.Audio16k
import dev.jarvis.link.logic.HotwordGate
import dev.jarvis.link.logic.Outcome
import dev.jarvis.link.logic.TalkService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.atomic.AtomicBoolean

/** Bind point that makes JarvisLink selectable as the system digital assistant. */
class JarvisInteractionService : VoiceInteractionService()

class JarvisSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = JarvisSession(this)
}

/**
 * One assistant invocation: a plain plate with status, transcript and
 * Listen/Close buttons. Records one utterance, sends it to the bridge
 * `/talk`, shows and speaks the reply. The mic is released on hide/destroy.
 */
class JarvisSession(ctx: Context) : VoiceInteractionSession(ctx) {
    private val graph = JarvisApp.graph(ctx)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val recorder = Audio16k.Recorder(ctx)
    private var job: Job? = null
    private lateinit var status: TextView
    private lateinit var transcript: TextView
    private lateinit var listen: Button

    override fun onCreateContentView(): View {
        val v = layoutInflater.inflate(R.layout.session_assistant, null, false)
        status = v.findViewById(R.id.session_status)
        transcript = v.findViewById(R.id.session_transcript)
        listen = v.findViewById(R.id.session_listen)
        v.findViewById<Button>(R.id.session_close).setOnClickListener { hide() }
        listen.setOnClickListener { startExchange() }
        return v
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        HotwordGate.sessionActive = true
        val c = graph.config()
        if (c.offline) { show("Offline mode", "Turn off offline mode in JarvisLink Settings."); listen.isEnabled = false; return }
        c.problem()?.let { show("Not linked", it.message); listen.isEnabled = false; return }
        listen.isEnabled = true
        startExchange()
    }

    override fun onHide() {
        super.onHide()
        release()
    }

    override fun onDestroy() {
        release()
        scope.cancel()
        super.onDestroy()
    }

    private fun release() {
        HotwordGate.sessionActive = false
        job?.cancel()
        job = null
        try { recorder.stop() } catch (_: Exception) { }
    }

    private fun show(s: String, t: String) { status.text = s; transcript.text = t }

    private fun startExchange() {
        if (job?.isActive == true) return
        job = scope.launch {
            if (!Audio16k.hasMicPermission(context)) {
                show("Microphone blocked", "Open JarvisLink and grant the microphone permission.")
                return@launch
            }
            listen.isEnabled = false
            show("Listening...", "Speak now.")
            if (!recorder.start()) {
                show("Microphone unavailable", "The microphone could not start.")
                listen.isEnabled = true
                return@launch
            }
            delay(LISTEN_MS)
            val pcm = recorder.stop()
            show("Thinking...", "")
            when (val r = withContext(Dispatchers.IO) { graph.talkService.exchange(pcm) }) {
                is Outcome.Ok -> show("Jarvis", "You: ${r.value.transcript}\n\nJarvis: ${r.value.display}")
                is Outcome.Fail -> show("Jarvis", r.message)
            }
            listen.isEnabled = true
        }
    }

    private companion object {
        const val LISTEN_MS = 6000L
    }
}

/**
 * System speech recognizer backed by bridge `/talk` (required by the
 * assistant role). One utterance at a time; every path ends in results or
 * an error so callers never hang, and the mic is held only for the utterance.
 */
class JarvisRecognitionService : RecognitionService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var recorder: Audio16k.Recorder? = null
    private var job: Job? = null
    private var done: AtomicBoolean = AtomicBoolean(true)

    override fun onStartListening(recognizerIntent: Intent, listener: Callback) {
        val graph = JarvisApp.graph(this)
        if (graph.config().offline || !graph.config().configured) {
            listener.error(SpeechRecognizer.ERROR_CLIENT)
            return
        }
        if (!Audio16k.hasMicPermission(this)) {
            listener.error(SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS)
            return
        }
        if (!done.get()) {
            listener.error(SpeechRecognizer.ERROR_RECOGNIZER_BUSY)
            return
        }
        done = AtomicBoolean(false)
        val mine = done
        val rec = Audio16k.Recorder(this, 20).also { recorder = it }
        if (!rec.start()) {
            mine.set(true)
            listener.error(SpeechRecognizer.ERROR_AUDIO)
            return
        }
        listener.readyForSpeech(Bundle())
        listener.beginningOfSpeech()
        job = scope.launch {
            delay(WINDOW_MS)
            finish(mine, rec, listener)
        }
    }

    override fun onStopListening(listener: Callback) {
        val rec = recorder ?: return
        scope.launch { finish(done, rec, listener) }
    }

    override fun onCancel(listener: Callback) {
        job?.cancel()
        if (done.compareAndSet(false, true)) {
            try { recorder?.stop() } catch (_: Exception) { }
        }
        listener.error(SpeechRecognizer.ERROR_CLIENT)
    }

    override fun onDestroy() {
        job?.cancel()
        if (done.compareAndSet(false, true)) try { recorder?.stop() } catch (_: Exception) { }
        scope.cancel()
        super.onDestroy()
    }

    /** Single winner per utterance: timer, stop and cancel race safely. */
    private suspend fun finish(flag: AtomicBoolean, rec: Audio16k.Recorder, listener: Callback) {
        if (!flag.compareAndSet(false, true)) return
        job?.cancel()
        val pcm = rec.stop()
        val graph = JarvisApp.graph(this)
        try {
            listener.endOfSpeech()
        } catch (_: Exception) { }
        when (val r = withContext(Dispatchers.IO) { graph.talkService.exchange(pcm, play = false) }) {
            is Outcome.Ok -> {
                val b = Bundle()
                b.putStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION, arrayListOf(r.value.transcript))
                try { listener.results(b) } catch (_: Exception) { }
            }
            is Outcome.Fail -> try {
                listener.error(
                    when {
                        r.code == TalkService.CODE_NO_MATCH || r.code == TalkService.CODE_TOO_SHORT ->
                            SpeechRecognizer.ERROR_NO_MATCH
                        r.transient -> SpeechRecognizer.ERROR_NETWORK
                        else -> SpeechRecognizer.ERROR_SERVER
                    },
                )
            } catch (_: Exception) { }
        }
    }

    private companion object {
        const val WINDOW_MS = 8000L
    }
}
