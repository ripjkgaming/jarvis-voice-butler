package dev.jarvis.link

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.NotificationCompat
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import java.util.concurrent.atomic.AtomicLong

/** One local approval request. */
data class Approval(val id: Long, val title: String, val detail: String)

/** In-memory approval queue (a server queue needs a bridge endpoint). */
object ApprovalStore {
    private val seq = AtomicLong(1)
    private val items = mutableListOf<Approval>()
    private val decided = mutableMapOf<Long, Boolean>()

    @Synchronized
    fun add(title: String, detail: String): Approval {
        val a = Approval(seq.getAndIncrement(), title, detail)
        items.add(a)
        return a
    }

    @Synchronized
    fun list(): List<Approval> = items.toList()

    @Synchronized
    fun decide(id: Long, approved: Boolean): Boolean {
        val i = items.indexOfFirst { it.id == id }
        if (i < 0) return false
        items.removeAt(i)
        decided[id] = approved
        return true
    }

    @Synchronized
    fun verdict(id: Long): Boolean? = decided[id]
}

/**
 * One-tap Approve/Deny from the notification shade: decides the request,
 * cancels the ask, and posts the confirmation as a notification.
 */
class ApprovalReceiver : BroadcastReceiver() {
    companion object {
        const val ACTION = "dev.jarvis.link.APPROVAL_DECIDE"
        const val EXTRA_ID = "id"
        const val EXTRA_OK = "ok"
        const val CH = "jarvis_approvals"
        private const val BASE_NOTIF = 100

        fun ask(ctx: Context, approval: Approval) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(CH, "Jarvis approvals", NotificationManager.IMPORTANCE_HIGH)
            )
            val deny = PendingIntent.getBroadcast(
                ctx, approval.id.toInt() * 2,
                Intent(ctx, ApprovalReceiver::class.java).setAction(ACTION)
                    .putExtra(EXTRA_ID, approval.id).putExtra(EXTRA_OK, false),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val ok = PendingIntent.getBroadcast(
                ctx, approval.id.toInt() * 2 + 1,
                Intent(ctx, ApprovalReceiver::class.java).setAction(ACTION)
                    .putExtra(EXTRA_ID, approval.id).putExtra(EXTRA_OK, true),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            nm.notify(
                BASE_NOTIF + (approval.id % 1000).toInt(),
                NotificationCompat.Builder(ctx, CH)
                    .setSmallIcon(android.R.drawable.ic_dialog_alert)
                    .setContentTitle(approval.title)
                    .setContentText(approval.detail)
                    .addAction(0, "Deny", deny)
                    .addAction(0, "Approve", ok)
                    .setAutoCancel(true)
                    .build(),
            )
        }
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != ACTION) return
        val id = intent.getLongExtra(EXTRA_ID, -1)
        val ok = intent.getBooleanExtra(EXTRA_OK, false)
        if (!ApprovalStore.decide(id, ok)) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.cancel(BASE_NOTIF + (id % 1000).toInt())
        nm.notify(
            BASE_NOTIF + 900 + (id % 99).toInt(),
            NotificationCompat.Builder(ctx, CH)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(if (ok) "Approved" else "Denied")
                .setContentText("Request #$id ${if (ok) "approved" else "denied"}.")
                .setAutoCancel(true)
                .build(),
        )
    }
}

/** Approvals tab: local queue with Approve/Deny + shade actions. */
class ApprovalsFragment : Fragment() {
    private lateinit var adapter: Adapter
    private var items: List<Approval> = emptyList()

    inner class Adapter : RecyclerView.Adapter<Adapter.Holder>() {
        inner class Holder(val root: LinearLayout) : RecyclerView.ViewHolder(root)

        override fun getItemCount() = items.size
        override fun onCreateViewHolder(parent: ViewGroup, type: Int): Holder {
            val root = LinearLayout(parent.context).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(20, 14, 20, 14)
                background = androidx.core.content.ContextCompat.getDrawable(
                    context, R.drawable.hud_panel
                )
            }
            val tv = TextView(parent.context).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                setTextColor(0xFFE8F6FF.toInt())
            }
            val deny = Button(parent.context).apply {
                text = "Deny"
                setTextColor(0xFFFF8A80.toInt())
            }
            val ok = Button(parent.context).apply { text = "Approve" }
            root.addView(tv)
            root.addView(deny)
            root.addView(ok)
            val h = Holder(root)
            deny.setOnClickListener { decide(h.bindingAdapterPosition, false) }
            ok.setOnClickListener { decide(h.bindingAdapterPosition, true) }
            return h
        }

        override fun onBindViewHolder(h: Holder, position: Int) {
            val a = items[position]
            (h.root.getChildAt(0) as TextView).text = "#${a.id} ${a.title}\n${a.detail}"
        }
    }

    private fun decide(position: Int, ok: Boolean) {
        if (position < 0 || position >= items.size) return
        ApprovalStore.decide(items[position].id, ok)
        refresh()
    }

    private fun refresh() {
        items = ApprovalStore.list()
        adapter.notifyDataSetChanged()
    }

    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_approvals, host, false)
        val list = v.findViewById<RecyclerView>(R.id.approvals_list)
        adapter = Adapter()
        list.layoutManager = LinearLayoutManager(requireContext())
        list.adapter = adapter
        list.layoutAnimation = android.view.animation.AnimationUtils.loadLayoutAnimation(
            requireContext(), R.anim.hud_list
        )
        refresh()
        v.findViewById<Button>(R.id.approvals_demo).setOnClickListener {
            val a = ApprovalStore.add("Lock the PC?", "Demo request from the Approvals tab.")
            ApprovalReceiver.ask(requireContext(), a)
            refresh()
        }
        return v
    }

    override fun onResume() {
        super.onResume()
        if (::adapter.isInitialized) refresh()
    }
}
