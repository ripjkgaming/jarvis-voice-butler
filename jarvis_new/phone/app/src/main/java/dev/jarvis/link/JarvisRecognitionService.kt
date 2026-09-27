package dev.jarvis.link

import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * System speech recognizer: captures one utterance and transcribes it via
 * the PC bridge /talk endpoint (faster-whisper server-side), returning the
 * transcript through the standard SpeechRecognizer result bundle.
 *
 * Required by the assistant-role qualification (the role controller
 * demands a non-empty recognitionService alongside the session service).
 * Runs off the main thread; every path ends in onResults/onError so
 * callers never hang. The mic is held only for the utterance.
 */
class JarvisRecognitionService : RecognitionService() {

    private val recorder = Audio16k.Recorder()
    private val generation = AtomicInteger(0)
    private val busy = AtomicBoolean(false)
    @Volatile private var worker: Thread? = null

    override fun onStartListening(recognizerIntent: Intent, listener: RecognitionService.Callback) {
        val prefs = Prefs(this)
        if (!prefs.configured) {
            listener.error(SpeechRecognizer.ERROR_CLIENT)
            return
        }
        // One utterance at a time; a new request retires the old one.
        val gen = generation.incrementAndGet()
        stopRecorder()
        worker?.interrupt()
        if (!busy.compareAndSet(false, true)) {
            listener.error(SpeechRecognizer.ERROR_RECOGNIZER_BUSY)
            return
        }
        listener.readyForSpeech(Bundle())
        if (!recorder.start()) {
            busy.set(false)
            listener.error(SpeechRecognizer.ERROR_AUDIO)
            return
        }
        listener.beginningOfSpeech()
        worker = Thread {
            try {
                // Fixed window; onStopListening/onCancel can end it early.
                var waited = 0
                while (waited < 8000 && generation.get() == gen && !Thread.currentThread().isInterrupted) {
                    Thread.sleep(200)
                    waited += 200
                }
                if (generation.get() != gen || Thread.currentThread().isInterrupted) return@Thread
                finishOnce(gen, listener)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            } finally {
                if (generation.get() == gen) busy.set(false)
            }
        }.also { it.isDaemon = true; it.start() }
    }

    override fun onCancel(listener: RecognitionService.Callback) {
        generation.incrementAndGet()
        stopRecorder()
        worker?.interrupt()
        worker = null
        busy.set(false)
        listener.error(SpeechRecognizer.ERROR_CLIENT)
    }

    override fun onStopListening(listener: RecognitionService.Callback) {
        // End the window now and process what was captured. finishOnce is
        // single-winner: whichever of this and the worker loop gets there
        // first owns the result.
        finishOnce(generation.get(), listener)
    }

    override fun onDestroy() {
        generation.incrementAndGet()
        stopRecorder()
        worker?.interrupt()
        worker = null
        busy.set(false)
        super.onDestroy()
    }

    private fun stopRecorder() {
        try { recorder.stopToBase64(1) } catch (_: Exception) { }
    }

    private fun finishOnce(gen: Int, listener: RecognitionService.Callback) {
        if (!generation.compareAndSet(gen, gen + 1)) return
        try {
            val audio: String
            try {
                audio = recorder.stopToBase64()
            } catch (_: Exception) {
                listener.error(SpeechRecognizer.ERROR_AUDIO)
                return
            }
            if (audio.isEmpty()) {
                listener.error(SpeechRecognizer.ERROR_NO_MATCH)
                return
            }
            try {
                val reply = LinkApi(Prefs(this)).talk(audio)
                RemoteLauncher.handleAction(this, reply.action)
                val text = reply.transcript.trim()
                if (text.isEmpty()) {
                    listener.error(SpeechRecognizer.ERROR_NO_MATCH)
                    return
                }
                listener.endOfSpeech()
                val results = Bundle()
                results.putStringArrayList(
                    SpeechRecognizer.RESULTS_RECOGNITION, arrayListOf(text)
                )
                listener.results(results)
                // Offer the voice reply for bugtests (same probe as sessions).
                SessionAudioProbe.offer(reply.audioB64, reply.audioRate)
            } catch (_: LinkApi.ApiException) {
                listener.error(SpeechRecognizer.ERROR_NETWORK)
            } catch (_: Exception) {
                listener.error(SpeechRecognizer.ERROR_SPEECH_TIMEOUT)
            }
        } finally {
            busy.set(false)
        }
    }
}
