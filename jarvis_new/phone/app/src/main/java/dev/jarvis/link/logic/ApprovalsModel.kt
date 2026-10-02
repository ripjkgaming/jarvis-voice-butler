package dev.jarvis.link.logic

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** One approval request. */
data class Approval(val id: Long, val title: String, val detail: String)

/** Notification side effects for approvals (shade Approve/Deny). */
interface ApprovalNotifier {
    fun ask(a: Approval)
    fun cancel(id: Long)
    fun confirm(id: Long, approved: Boolean)
}

/**
 * Local approval queue (the bridge has no approvals endpoint). Decisions
 * from the screen or from the notification shade both land here; the
 * verdict of every decided request is kept for the session.
 */
class ApprovalStore(private val notifier: ApprovalNotifier? = null) {
    private var seq = 1L
    private val _pending = MutableStateFlow<List<Approval>>(emptyList())
    val pending: StateFlow<List<Approval>> = _pending.asStateFlow()
    private val verdicts = HashMap<Long, Boolean>()

    @Synchronized
    fun request(title: String, detail: String): Approval {
        val a = Approval(seq++, title.trim().ifEmpty { "Approval needed" }, detail.trim())
        _pending.value = _pending.value + a
        notifier?.ask(a)
        return a
    }

    /** True when the request was pending (and is now decided). */
    @Synchronized
    fun decide(id: Long, approved: Boolean): Boolean {
        val cur = _pending.value
        if (cur.none { it.id == id }) return false
        _pending.value = cur.filterNot { it.id == id }
        verdicts[id] = approved
        notifier?.cancel(id)
        notifier?.confirm(id, approved)
        return true
    }

    @Synchronized
    fun verdict(id: Long): Boolean? = verdicts[id]
}
