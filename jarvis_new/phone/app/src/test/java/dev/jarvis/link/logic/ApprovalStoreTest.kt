package dev.jarvis.link.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeNotifier : ApprovalNotifier {
    val asked = mutableListOf<Approval>()
    val cancelled = mutableListOf<Long>()
    val confirmed = mutableListOf<Pair<Long, Boolean>>()
    override fun ask(a: Approval) { asked.add(a) }
    override fun cancel(id: Long) { cancelled.add(id) }
    override fun confirm(id: Long, approved: Boolean) { confirmed.add(id to approved) }
}

class ApprovalStoreTest {
    @Test
    fun requestIdsIncrease() {
        val s = ApprovalStore()
        val a = s.request("t1", "d1")
        val b = s.request("t2", "d2")
        assertEquals(1L, a.id)
        assertEquals(2L, b.id)
        assertEquals(listOf(a, b), s.pending.value)
    }

    @Test
    fun blankTitleDefaults() {
        val s = ApprovalStore()
        assertEquals("Approval needed", s.request("  ", "d").title)
    }

    @Test
    fun decideRemovesAndRecordsVerdict() {
        val s = ApprovalStore()
        val a = s.request("t", "d")
        s.request("t2", "d2")
        assertTrue(s.decide(a.id, true))
        assertEquals(1, s.pending.value.size)
        assertEquals(true, s.verdict(a.id))
    }

    @Test
    fun decideUnknownOrTwiceReturnsFalse() {
        val s = ApprovalStore()
        assertFalse(s.decide(999L, true))
        val a = s.request("t", "d")
        assertTrue(s.decide(a.id, false))
        assertFalse(s.decide(a.id, true))
        assertEquals(false, s.verdict(a.id))
    }

    @Test
    fun unknownVerdictIsNull() {
        assertNull(ApprovalStore().verdict(1L))
    }

    @Test
    fun notifierRecordsAskCancelConfirm() {
        val n = FakeNotifier()
        val s = ApprovalStore(n)
        val a = s.request("title", "detail")
        assertEquals(listOf(a), n.asked)
        assertTrue(s.decide(a.id, true))
        assertEquals(listOf(a.id), n.cancelled)
        assertEquals(listOf(a.id to true), n.confirmed)
    }
}
