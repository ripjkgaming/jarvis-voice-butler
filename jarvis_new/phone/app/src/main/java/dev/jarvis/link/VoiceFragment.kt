package dev.jarvis.link

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

/**
 * Voice tab — Kotlin port of Flutter `voice_screen.dart` + `voice_ctrl.dart`.
 *
 * Join via bridge-minted token (POST /token {room, dispatch}), mic live,
 * remote agent audio auto-plays, orb breathes with speakers, captions
 * stream below. Extras over Flutter: JOIN LAPTOP CALL (GET /room — join
 * the live laptop HUD room when one exists) and SUMMON LAPTOP
 * (POST /summon, wakes the laptop agent). The mic runs under
 * [VoiceCallService] (foreground microphone) from join to hangup.
 */
class VoiceFragment : Fragment(), VoiceCallManager.Listener {
    private var manager: VoiceCallManager? = null
    private var muted = false

    private var orb: ArcReactorView? = null
    private var statePill: TextView? = null
    private var speaking: TextView? = null
    private var detail: TextView? = null
    private var status: TextView? = null
    private var talkBtn: Button? = null
    private var muteBtn: Button? = null
    private var swipe: SwipeRefreshLayout? = null

    private val captions = mutableListOf<Pair<String, String>>()
    private lateinit var capAdapter: CapAdapter

    private val poll = object : Runnable {
        override fun run() {
            val m = manager
            if (m != null && m.live) {
                pullCaptions()
                view?.postDelayed(this, 3000)
            }
        }
    }

    inner class CapAdapter : RecyclerView.Adapter<CapAdapter.Holder>() {
        inner class Holder(val view: TextView) : RecyclerView.ViewHolder(view)

        override fun getItemCount() = captions.size
        override fun onCreateViewHolder(parent: ViewGroup, type: Int): Holder {
            val tv = TextView(parent.context).apply {
                setPadding(20, 14, 20, 14)
                textSize = 13f
                background = ContextCompat.getDrawable(context, R.drawable.hud_panel)
            }
            return Holder(tv)
        }

        override fun onBindViewHolder(h: Holder, position: Int) {
            val (role, text) = captions[position]
            h.view.text = (if (role == "jarvis") "J.A.R.V.I.S.: " else "YOU: ") + text
            h.view.setTextColor(
                if (role == "jarvis") 0xFF5FE3FF.toInt() else 0xFFE8F6FF.toInt()
            )
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater, host: ViewGroup?, state: Bundle?
    ): View {
        val v = inflater.inflate(R.layout.fragment_voice, host, false)
        orb = v.findViewById(R.id.voice_orb)
        statePill = v.findViewById(R.id.voice_state)
        speaking = v.findViewById(R.id.voice_speaking)
        detail = v.findViewById(R.id.voice_detail)
        status = v.findViewById(R.id.voice_status)
        talkBtn = v.findViewById(R.id.btn_voice_talk)
        muteBtn = v.findViewById(R.id.btn_voice_mute)
        swipe = v.findViewById(R.id.voice_swipe)
        orb?.energy = 0.35f

        val m = VoiceCallManager(requireContext().applicationContext)
        m.listener = this
        manager = m

        capAdapter = CapAdapter()
        val list = v.findViewById<RecyclerView>(R.id.voice_captions)
        list.layoutManager = LinearLayoutManager(requireContext())
        list.adapter = capAdapter

        swipe?.setOnRefreshListener { pullCaptions() }
        talkBtn?.setOnClickListener {
            if (m.live) hangup() else startJoin()
        }
        muteBtn?.setOnClickListener {
            muted = !muted
            m.setMuted(muted)
            muteBtn?.text = if (muted) "UNMUTE" else "MUTE"
            detail?.text = if (m.live && muted) "MUTED — TAP MIC TO SPEAK" else detail?.text
        }
        v.findViewById<Button>(R.id.btn_voice_room).setOnClickListener { joinLaptopCall() }
        v.findViewById<Button>(R.id.btn_voice_summon).setOnClickListener { summonLaptop() }
        return v
    }

    override fun onDestroyView() {
        view?.removeCallbacks(poll)
        try {
            manager?.hangup()
        } catch (_: Exception) { }
        manager?.destroy()
        manager = null
        runCatching { VoiceCallService.stop(requireContext()) }
        orb = null
        super.onDestroyView()
    }

    // ── Join pipeline ────────────────────────────────────────────

    private fun startJoin(room: String = "", dispatch: Boolean = true) {
        val ctx = requireContext()
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        status?.text = "Minting token…"
        Thread {
            try {
                val minted = LinkApi(Prefs(ctx)).livekitToken(room, dispatch)
                val params = VoiceCall.connectParamsJson(minted)
                    ?: throw IllegalStateException(
                        minted.optString("error").ifEmpty { "token refused" }
                    )
                activity?.runOnUiThread {
                    VoiceCallService.start(ctx)
                    manager?.join(params.url, params.token)
                }
            } catch (e: Exception) {
                activity?.runOnUiThread {
                    status?.text = "Error: ${e.message}"
                    onState(VoiceCall.CallState.ERROR, e.message ?: "join failed")
                }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    override fun onRequestPermissionsResult(
        code: Int, perms: Array<out String>, results: IntArray
    ) {
        if (code == REQ_MIC &&
            results.isNotEmpty() && results[0] == PackageManager.PERMISSION_GRANTED
        ) {
            startJoin()
        } else if (code == REQ_MIC) {
            status?.text = "Mic permission denied — call needs RECORD_AUDIO"
        }
    }

    /** GET /room: join the live laptop HUD call when one exists. */
    private fun joinLaptopCall() {
        val ctx = requireContext()
        status?.text = "Checking laptop…"
        Thread {
            try {
                val r = LinkApi(Prefs(ctx)).room()
                val name = r.optString("room").ifEmpty { r.optString("name") }
                if (name.isEmpty()) {
                    activity?.runOnUiThread { status?.text = "No live laptop call" }
                    return@Thread
                }
                activity?.runOnUiThread {
                    status?.text = "Joining laptop call…"
                    startJoin(room = name, dispatch = false)
                }
            } catch (e: Exception) {
                activity?.runOnUiThread { status?.text = "Error: ${e.message}" }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    /** POST /summon: wake the laptop agent. */
    private fun summonLaptop() {
        val ctx = requireContext()
        status?.text = "Summoning…"
        Thread {
            try {
                val r = LinkApi(Prefs(ctx)).summon()
                RemoteLauncher.handleAction(context, r.optJSONObject("action"))
                activity?.runOnUiThread {
                    status?.text = if (r.optBoolean("ok", true)) "Summoned"
                    else "Summon failed: ${r.optString("error")}"
                }
            } catch (e: Exception) {
                activity?.runOnUiThread { status?.text = "Error: ${e.message}" }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    private fun hangup() {
        manager?.hangup()
        runCatching { VoiceCallService.stop(requireContext()) }
    }

    // ── VoiceCallManager.Listener (main thread) ──────────────────

    override fun onState(state: VoiceCall.CallState, detailText: String) {
        activity?.runOnUiThread { render(state, detailText) }
    }

    override fun onSpeaking(speakingNow: Boolean) {
        activity?.runOnUiThread {
            val live = manager?.live == true
            orb?.energy = when {
                speakingNow -> 1.0f
                live -> 0.45f
                else -> 0.35f
            }
            speaking?.text = when {
                speakingNow -> "● AGENT SPEAKING"
                live -> if (muted) "○ MUTED" else "○ LISTENING…"
                else -> "○ STANDBY"
            }
            speaking?.setTextColor(
                if (speakingNow) 0xFF5FE3FF.toInt() else 0xFF5A7A8C.toInt()
            )
        }
    }

    private fun render(state: VoiceCall.CallState, detailText: String) {
        val live = state == VoiceCall.CallState.LIVE
        val joining = state == VoiceCall.CallState.JOINING
        val error = state == VoiceCall.CallState.ERROR
        statePill?.text = when (state) {
            VoiceCall.CallState.IDLE -> "IDLE"
            VoiceCall.CallState.JOINING -> "JOINING"
            VoiceCall.CallState.LIVE -> "LIVE"
            VoiceCall.CallState.ERROR -> "FAULT"
        }
        detail?.text = when (state) {
            VoiceCall.CallState.IDLE -> "Tap to summon Jarvis"
            VoiceCall.CallState.JOINING ->
                if (detailText.isEmpty()) "JOINING…" else detailText.uppercase()
            VoiceCall.CallState.LIVE ->
                if (muted) "MUTED — TAP MIC TO SPEAK" else "LISTENING…"
            VoiceCall.CallState.ERROR ->
                "ERROR: " + if (detailText.isEmpty()) "UPLINK FAILED" else detailText.uppercase()
        }
        detail?.setTextColor(
            if (error) 0xFFFFB648.toInt() else 0xFF5A7A8C.toInt()
        )
        talkBtn?.text = if (live) "✆ HANG UP" else "◉ TALK"
        talkBtn?.isEnabled = !joining
        if (live) {
            view?.removeCallbacks(poll)
            view?.post(poll)
            pullCaptions()
        } else {
            view?.removeCallbacks(poll)
            if (state != VoiceCall.CallState.JOINING) {
                runCatching { VoiceCallService.stop(requireContext()) }
            }
        }
        if (error) status?.text = detailText
    }

    // ── Live transcript (GET /captions, newest last like Flutter) ──

    private fun pullCaptions() {
        val ctx = context ?: return
        Thread {
            try {
                val caps = LinkApi(Prefs(ctx)).captions(8)
                val raw = caps.optJSONArray("captions") ?: return@Thread
                val rows = mutableListOf<Pair<String, String>>()
                for (i in 0 until raw.length()) {
                    val m = raw.optJSONObject(i) ?: continue
                    rows.add(m.optString("role") to m.optString("text"))
                }
                rows.reverse()
                activity?.runOnUiThread {
                    captions.clear()
                    captions.addAll(rows)
                    capAdapter.notifyDataSetChanged()
                    swipe?.isRefreshing = false
                }
            } catch (_: Exception) {
                activity?.runOnUiThread { swipe?.isRefreshing = false }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    companion object {
        private const val REQ_MIC = 77
    }
}
