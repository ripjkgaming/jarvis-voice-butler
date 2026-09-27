package dev.jarvis.link

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Home tab (v1.5): arc-reactor HUD hero, live clock/date, power-core
 * battery readout, tap-to-talk mic, prompt bar, quick actions, live
 * chat stream, and the offline input-lock banner. The stream persists
 * (last 30) in Prefs.chatStream; every send bumps eventCursor.
 */
class HomeFragment : Fragment() {
    data class Msg(val who: String, val text: String)

    private val messages = mutableListOf<Msg>()
    private lateinit var adapter: Adapter
    private var orb: ArcReactorView? = null
    private var out: TextView? = null
    private var micBtn: Button? = null
    private var sendBtn: Button? = null
    private var clockView: TextView? = null
    private var dateView: TextView? = null
    private var busy = AtomicBoolean(false)
    private val recorder = Audio16k.Recorder()

    private val clockTick = object : Runnable {
        override fun run() {
            val v = view ?: return
            try {
                val now = java.util.Date()
                clockView?.text =
                    java.text.SimpleDateFormat("H:mm", java.util.Locale.US).format(now)
                dateView?.text =
                    java.text.SimpleDateFormat("dd MMM yyyy", java.util.Locale.US)
                        .format(now).uppercase()
            } catch (_: Exception) { }
            v.postDelayed(this, 20_000)
        }
    }

    inner class Adapter : RecyclerView.Adapter<Adapter.Holder>() {
        inner class Holder(val view: TextView) : RecyclerView.ViewHolder(view)

        override fun getItemCount() = messages.size
        override fun onCreateViewHolder(parent: ViewGroup, type: Int): Holder {
            val tv = TextView(parent.context).apply {
                setPadding(28, 20, 28, 20)
                textSize = 14f
                background = androidx.core.content.ContextCompat.getDrawable(
                    context, R.drawable.hud_panel
                )
            }
            return Holder(tv)
        }

        override fun onBindViewHolder(h: Holder, position: Int) {
            val m = messages[position]
            h.view.text = "${m.who}: ${m.text}"
            h.view.setTextColor(
                if (m.who == "Jarvis") 0xFF9BE9FF.toInt() else 0xFFE8F6FF.toInt()
            )
        }
    }

    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_home, host, false)
        orb = v.findViewById(R.id.home_orb)
        out = v.findViewById(R.id.home_out)
        micBtn = v.findViewById(R.id.home_mic)
        sendBtn = v.findViewById(R.id.home_send)
        val banner = v.findViewById<TextView>(R.id.home_banner)
        val prompt = v.findViewById<EditText>(R.id.home_prompt)
        val list = v.findViewById<RecyclerView>(R.id.home_stream)
        adapter = Adapter()
        list.layoutManager = LinearLayoutManager(requireContext()).apply { stackFromEnd = true }
        list.adapter = adapter
        list.layoutAnimation = android.view.animation.AnimationUtils.loadLayoutAnimation(
            requireContext(), R.anim.hud_list
        )
        TtsManager.init(requireContext())
        loadStream()
        val offline = Prefs(requireContext()).offlineMode
        banner.visibility = if (offline) View.VISIBLE else View.GONE
        micBtn?.isEnabled = !offline
        sendBtn?.isEnabled = !offline
        micBtn?.setOnClickListener { startAutoExchange() }
        sendBtn?.setOnClickListener {
            val text = prompt.text.toString().trim()
            if (text.isEmpty()) return@setOnClickListener
            prompt.setText("")
            sendText(text)
        }
        v.findViewById<Button>(R.id.home_route).setOnClickListener {
            val text = prompt.text.toString().trim()
            if (text.isEmpty()) return@setOnClickListener
            prompt.setText("")
            sendInstant(text)
        }
        v.findViewById<Button>(R.id.home_mute).setOnClickListener { toggleMute() }
        v.findViewById<Button>(R.id.home_shot).setOnClickListener { runTool("Shot") { it.tool("screenshot") } }
        v.findViewById<Button>(R.id.home_notify).setOnClickListener {
            runTool("Ping") {
                it.tool("notify", org.json.JSONObject().put("body", "Ping from phone"))
            }
        }
        val unlockBtn = v.findViewById<Button>(R.id.home_unlock)
        // Guest mode locks the laptop only (never the phone): the PC stays
        // locked, so unlock is disabled here — only the owner's fingerprint
        // outside guest mode opens it.
        unlockBtn.isEnabled = !Prefs(requireContext()).guestMode
        unlockBtn.setOnClickListener {
            if (Prefs(requireContext()).guestMode) {
                out?.text = "Unlock denied: guest mode keeps the PC locked"
                return@setOnClickListener
            }
            unlockWithFingerprint()
        }
        clockView = v.findViewById(R.id.home_clock)
        dateView = v.findViewById(R.id.home_date)
        v.post(clockTick)
        updatePower(v)
        v.findViewById<TextView>(R.id.home_status)?.startAnimation(
            android.view.animation.AnimationUtils.loadAnimation(requireContext(), R.anim.pulse)
        )
        return v
    }

    override fun onResume() {
        super.onResume()
        // Guest may have been toggled in Settings — refresh the gate.
        try {
            view?.findViewById<Button>(R.id.home_unlock)?.isEnabled =
                !Prefs(requireContext()).guestMode
        } catch (_: Exception) { }
    }

    override fun onDestroyView() {
        view?.removeCallbacks(clockTick)
        clockView = null
        dateView = null
        super.onDestroyView()
    }

    /** Power-core readout: real phone battery % + charging state. */
    private fun updatePower(v: View) {
        try {
            val bm = requireContext().getSystemService(android.content.Context.BATTERY_SERVICE)
                as android.os.BatteryManager
            val pct = bm.getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY)
            v.findViewById<TextView>(R.id.home_battery).text = "POWER $pct %"
            val sticky = requireContext().registerReceiver(
                null, android.content.IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED)
            )
            val st = sticky?.getIntExtra(
                android.os.BatteryManager.EXTRA_STATUS, -1
            ) ?: -1
            val charging = st == android.os.BatteryManager.BATTERY_STATUS_CHARGING ||
                st == android.os.BatteryManager.BATTERY_STATUS_FULL
            v.findViewById<TextView>(R.id.home_charge).text =
                if (charging) "— CHARGING —" else "— STANDBY —"
        } catch (_: Exception) { }
    }

    /** WAKE routing entry: unlocked screens start talking at once. */
    fun startAutoExchange() {
        if (!busy.compareAndSet(false, true)) return
        if (Prefs(requireContext()).offlineMode) {
            busy.set(false)
            return
        }
        HotwordGate.pttHeld = true
        out?.text = "Listening…"
        orb?.energy = 0.8f
        Thread {
            try {
                if (!recorder.start()) throw IllegalStateException("Mic unavailable")
                Thread.sleep(6000)
                val audio = recorder.stopToBase64()
                uiTalk(audio)
            } catch (e: Exception) {
                activity?.runOnUiThread {
                    out?.text = "Error: ${e.message}"
                    orb?.energy = 0.35f
                }
                HotwordGate.pttHeld = false
                busy.set(false)
            }
        }.also { it.isDaemon = true; it.start() }
    }

    private fun uiTalk(audio: String) {
        try {
            val r = LinkApi(Prefs(requireContext())).talk(audio)
            RemoteLauncher.handleAction(context, r.action)
            val replyText = r.reply.ifEmpty { r.warning ?: "(no reply)" }
            activity?.runOnUiThread {
                push("You", r.transcript.ifEmpty { "(nothing heard)" })
                push("Jarvis", replyText)
                out?.text = replyText
                orb?.energy = if (r.audioB64.isNotEmpty()) 1.0f else 0.35f
                if (r.audioB64.isNotEmpty()) {
                    try {
                        Audio16k.playPcm16(r.audioB64, r.audioRate)
                    } catch (_: Exception) { }
                } else {
                    TtsManager.speak(replyText)
                }
            }
        } catch (e: Exception) {
            activity?.runOnUiThread { out?.text = "Error: ${e.message}" }
        } finally {
            HotwordGate.pttHeld = false
            busy.set(false)
        }
    }

    /** ⚡ insta-command: /route first (ms), transparent fallback to chat. */
    private fun sendInstant(text: String) {
        if (!busy.compareAndSet(false, true)) return
        push("You", text)
        out?.text = "⚡ routing…"
        orb?.energy = 0.8f
        Thread {
            var fellBack = false
            try {
                val t0 = android.os.SystemClock.elapsedRealtime()
                val routed = LinkApi(Prefs(requireContext())).routeFull(text)
                RemoteLauncher.handleAction(context, routed.action)
                val reply = routed.reply
                val ms = android.os.SystemClock.elapsedRealtime() - t0
                activity?.runOnUiThread {
                    push("Jarvis", reply)
                    out?.text = "$reply  ⚡${ms}ms"
                    orb?.energy = 0.35f
                    TtsManager.speak(reply)
                }
            } catch (e: LinkApi.NoRouteException) {
                fellBack = true
                activity?.runOnUiThread {
                    out?.text = "No instant route — asking…"
                    sendText(text)
                }
            } catch (e: Exception) {
                activity?.runOnUiThread { out?.text = "Error: ${e.message}" }
            } finally {
                if (!fellBack) busy.set(false)
            }
        }.also { it.isDaemon = true; it.start() }
    }

    private fun sendText(text: String) {
        if (!busy.compareAndSet(false, true)) return
        push("You", text)
        out?.text = "Thinking…"
        orb?.energy = 0.5f
        Thread {
            try {
                // Last 20 messages (10 exchanges), like Flutter historyOf.
                val pairs = ChatHistory.historyOf(messages.map { it.who to it.text })
                val hist = ChatHistory.toJson(pairs)
                val res = LinkApi(Prefs(requireContext())).chat(text, hist)
                RemoteLauncher.handleAction(context, res.action)
                val reply = res.reply
                activity?.runOnUiThread {
                    push("Jarvis", reply)
                    out?.text = reply
                    orb?.energy = 0.35f
                    TtsManager.speak(reply)
                }
            } catch (e: Exception) {
                activity?.runOnUiThread { out?.text = "Error: ${e.message}" }
            } finally {
                busy.set(false)
            }
        }.also { it.isDaemon = true; it.start() }
    }

    /** Fingerprint-gated PC unlock, no confirm dialog: biometrics IS the
     * confirmation (a stolen unlocked phone alone must never open the PC).
     * Refused outright in guest mode — the laptop stays locked. */
    private fun unlockWithFingerprint() {
        if (Prefs(requireContext()).guestMode) {
            out?.text = "Unlock denied: guest mode keeps the PC locked"
            return
        }
        val mgr = androidx.biometric.BiometricManager.from(requireContext())
        if (mgr.canAuthenticate(
                androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
            ) != androidx.biometric.BiometricManager.BIOMETRIC_SUCCESS
        ) {
            out?.text = "Unlock denied: fingerprint unavailable"
            return
        }
        androidx.biometric.BiometricPrompt(
            this,
            androidx.core.content.ContextCompat.getMainExecutor(requireContext()),
            object : androidx.biometric.BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(
                    result: androidx.biometric.BiometricPrompt.AuthenticationResult
                ) {
                    runTool("Unlock") { it.tool("unlock") }
                }

                override fun onAuthenticationFailed() {
                    out?.text = "Unlock denied: fingerprint not recognized"
                }

                override fun onAuthenticationError(code: Int, msg: CharSequence) {
                    out?.text = "Unlock cancelled"
                }
            },
        ).authenticate(
            androidx.biometric.BiometricPrompt.PromptInfo.Builder()
                .setTitle("Unlock the PC?")
                .setSubtitle("Confirm it's you, Sir")
                .setNegativeButtonText("Cancel")
                .setAllowedAuthenticators(
                    androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
                )
                .build()
        )
    }

    private fun toggleMute() {
        Thread {
            try {
                val api = LinkApi(Prefs(requireContext()))
                val muted = api.micStatus().optBoolean("muted", false)
                api.setMuted(!muted)
                activity?.runOnUiThread {
                    (view?.findViewById<Button>(R.id.home_mute))?.text =
                        if (!muted) "UNMUTE" else "MUTE"
                }
            } catch (e: Exception) {
                activity?.runOnUiThread { out?.text = "Error: ${e.message}" }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    private fun runTool(label: String, fn: (LinkApi) -> org.json.JSONObject) {
        out?.text = "$label…"
        Thread {
            try {
                val r = fn(LinkApi(Prefs(requireContext())))
                RemoteLauncher.handleAction(context, r.optJSONObject("action"))
                activity?.runOnUiThread {
                    out?.text = if (r.optBoolean("ok", true)) "$label OK"
                    else "$label failed: ${r.optString("error")}"
                }
            } catch (e: Exception) {
                activity?.runOnUiThread { out?.text = "Error: ${e.message}" }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    private fun push(who: String, text: String) {
        messages.add(Msg(who, text))
        while (messages.size > 30) messages.removeAt(0)
        adapter.notifyDataSetChanged()
        persistStream()
        val prefs = Prefs(requireContext())
        prefs.eventCursor = prefs.eventCursor + 1
    }

    private fun loadStream() {
        try {
            val arr = org.json.JSONArray(Prefs(requireContext()).chatStream)
            for (i in 0 until arr.length()) {
                val row = arr.optJSONArray(i) ?: continue
                messages.add(Msg(row.optString(0, "?"), row.optString(1, "")))
            }
            adapter.notifyDataSetChanged()
        } catch (_: Exception) { }
        if (messages.isEmpty()) {
            messages.add(Msg("Jarvis", "At your service, Sir."))
            adapter.notifyDataSetChanged()
        }
    }

    private fun persistStream() {
        try {
            val arr = org.json.JSONArray()
            for (m in messages.takeLast(30)) {
                arr.put(org.json.JSONArray().put(m.who).put(m.text))
            }
            Prefs(requireContext()).chatStream = arr.toString()
        } catch (_: Exception) { }
    }
}
