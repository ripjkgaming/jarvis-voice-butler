package dev.jarvis.link

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

/**
 * Activity tab — Kotlin port of the Flutter captions/actions feeds
 * (home caption tail + /actions log): live PC command history
 * (GET /actions) plus the live transcript (GET /captions, newest last),
 * auto-refreshing every 5s with pull-to-refresh. Cost tracking needs a
 * bridge /costs endpoint that does not exist — the line says so instead
 * of inventing numbers.
 */
class ActivityFragment : Fragment() {
    private val lines = mutableListOf<String>()
    private val captions = mutableListOf<Pair<String, String>>()
    private lateinit var actionsAdapter: Adapter
    private lateinit var captionsAdapter: CapAdapter
    private var spend: TextView? = null
    private var swipe: SwipeRefreshLayout? = null
    private var refreshing = false

    private val poll = object : Runnable {
        override fun run() {
            if (isAdded) {
                refresh(quiet = true)
                view?.postDelayed(this, 5000)
            }
        }
    }

    inner class Adapter : RecyclerView.Adapter<Adapter.Holder>() {
        inner class Holder(val view: TextView) : RecyclerView.ViewHolder(view)

        override fun getItemCount() = lines.size
        override fun onCreateViewHolder(parent: ViewGroup, type: Int): Holder {
            val tv = TextView(parent.context).apply {
                setPadding(20, 14, 20, 14)
                textSize = 13f
                background = androidx.core.content.ContextCompat.getDrawable(
                    context, R.drawable.hud_panel
                )
            }
            return Holder(tv)
        }

        override fun onBindViewHolder(h: Holder, position: Int) {
            h.view.text = lines[position]
            h.view.setTextColor(0xFF9BE9FF.toInt())
        }
    }

    inner class CapAdapter : RecyclerView.Adapter<CapAdapter.Holder>() {
        inner class Holder(val view: TextView) : RecyclerView.ViewHolder(view)

        override fun getItemCount() = captions.size
        override fun onCreateViewHolder(parent: ViewGroup, type: Int): Holder {
            val tv = TextView(parent.context).apply {
                setPadding(20, 14, 20, 14)
                textSize = 13f
                background = androidx.core.content.ContextCompat.getDrawable(
                    context, R.drawable.hud_panel
                )
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
        val v = inflater.inflate(R.layout.fragment_activity, host, false)
        spend = v.findViewById(R.id.activity_spend)
        swipe = v.findViewById(R.id.activity_swipe)
        val list = v.findViewById<RecyclerView>(R.id.activity_list)
        actionsAdapter = Adapter()
        list.layoutManager = LinearLayoutManager(requireContext())
        list.adapter = actionsAdapter
        list.layoutAnimation = android.view.animation.AnimationUtils.loadLayoutAnimation(
            requireContext(), R.anim.hud_list
        )
        val caps = v.findViewById<RecyclerView>(R.id.activity_captions)
        captionsAdapter = CapAdapter()
        caps.layoutManager = LinearLayoutManager(requireContext())
        caps.adapter = captionsAdapter
        v.findViewById<Button>(R.id.activity_refresh).setOnClickListener { refresh() }
        swipe?.setOnRefreshListener { refresh() }
        refresh()
        return v
    }

    override fun onResume() {
        super.onResume()
        view?.postDelayed(poll, 5000)
    }

    override fun onPause() {
        view?.removeCallbacks(poll)
        super.onPause()
    }

    override fun onDestroyView() {
        spend = null
        swipe = null
        super.onDestroyView()
    }

    private fun refresh(quiet: Boolean = false) {
        if (refreshing) return
        refreshing = true
        if (!quiet) spend?.text = "Loading…"
        Thread {
            try {
                val api = LinkApi(Prefs(requireContext()))
                val items = try {
                    api.actions(100)
                } catch (_: Exception) {
                    emptyList()
                }
                val rows = try {
                    val arr = api.captions(20).optJSONArray("captions")
                    val out = mutableListOf<Pair<String, String>>()
                    if (arr != null) {
                        for (i in 0 until arr.length()) {
                            val m = arr.optJSONObject(i) ?: continue
                            out.add(m.optString("role") to m.optString("text"))
                        }
                        out.reverse()
                    }
                    out
                } catch (_: Exception) {
                    emptyList()
                }
                val prefs = Prefs(requireContext())
                prefs.eventCursor = prefs.eventCursor + 1
                activity?.runOnUiThread {
                    if (!isAdded) return@runOnUiThread
                    lines.clear()
                    lines.addAll(items)
                    actionsAdapter.notifyDataSetChanged()
                    captions.clear()
                    captions.addAll(rows)
                    captionsAdapter.notifyDataSetChanged()
                    spend?.text = "${items.size} logged actions · " +
                        "spend n/a (bridge has no /costs endpoint yet)"
                    swipe?.isRefreshing = false
                    refreshing = false
                }
            } catch (e: Exception) {
                activity?.runOnUiThread {
                    spend?.text = "Error: ${e.message}"
                    swipe?.isRefreshing = false
                    refreshing = false
                }
            }
        }.also { it.isDaemon = true; it.start() }
    }
}
