package dev.jarvis.link.logic
import org.junit.Assert.*
import org.junit.Test
import java.time.ZonedDateTime
class IncomingPolicyTest {
    private fun t(s: String) = ZonedDateTime.parse(s + "+08:00[Asia/Singapore]").toInstant().toEpochMilli()
    @Test fun schoolBoundariesAndWeekend() {
        assertFalse(IncomingPolicy.quiet(t("2026-10-05T07:59:59")))
        assertTrue(IncomingPolicy.quiet(t("2026-10-05T08:00:00")))
        assertTrue(IncomingPolicy.quiet(t("2026-10-05T15:29:59")))
        assertFalse(IncomingPolicy.quiet(t("2026-10-05T15:30:00")))
        assertFalse(IncomingPolicy.quiet(t("2026-10-04T10:00:00")))
    }
    @Test fun expiryClockSkewAndBoundaryTimer() {
        val n=t("2026-10-05T07:59:50")
        assertEquals(t("2026-10-05T08:00:00"),IncomingPolicy.ringUntil(n+45000,n))
        assertTrue(IncomingPolicy.valid(n,n+45000,n,n))
        assertFalse(IncomingPolicy.valid(n,n+45000,n+45000,n+45000))
        assertFalse(IncomingPolicy.valid(n,n+45000,n,n+16000))
        assertFalse(IncomingPolicy.valid(n+1000,n+45000,n,n))
        assertFalse(IncomingPolicy.valid(n,n+45001,n,n))
    }
    @Test fun monitorStatusExplainsTheGateThatStoppedDelivery() {
        val n=t("2026-10-04T10:00:00")
        assertEquals("monitoring",IncomingPolicy.monitorStatus(false,n,n,false))
        assertEquals("clock_skew",IncomingPolicy.monitorStatus(false,n,n+16000,false))
        assertEquals("call_active",IncomingPolicy.monitorStatus(false,n,n,true))
        val school=t("2026-10-05T08:00:00")
        assertEquals("quiet_hours",IncomingPolicy.monitorStatus(false,school,school,false))
        assertEquals("quiet_hours",IncomingPolicy.monitorStatus(true,n,n,false))
    }
    @Test fun fractionalEpochParsingKeepsStrict45SecondLifetime() {
        for (i in 0..9999) {
            val issued=1_791_081_590.0+i/10_000.0
            val expires=issued+45.0
            assertEquals(45_000,IncomingPolicy.epochMillis(expires)-IncomingPolicy.epochMillis(issued))
        }
    }
    @Test fun rejectionExplainsClockAndQuietGates() {
        val n=t("2026-10-04T10:00:00")
        assertEquals("clock_skew",IncomingPolicy.rejection(n,n+45000,n,n+16000))
        assertEquals("expired",IncomingPolicy.rejection(n,n+45000,n+45000,n+45000))
        val q=t("2026-10-05T08:00:00")
        assertEquals("quiet_hours",IncomingPolicy.rejection(q-1000,q+44000,q,q))
    }
}
