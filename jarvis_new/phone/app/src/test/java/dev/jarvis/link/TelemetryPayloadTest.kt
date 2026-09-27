package dev.jarvis.link

import org.junit.Assert.assertEquals
import org.junit.Test

class TelemetryPayloadTest {
    @Test
    fun normalValues() {
        assertEquals(
            "{\"battery\":75,\"charging\":true}",
            LinkApi.telemetryPayload(75, true),
        )
        assertEquals(
            "{\"battery\":12,\"charging\":false}",
            LinkApi.telemetryPayload(12, false),
        )
    }

    @Test
    fun boundaries() {
        assertEquals("{\"battery\":0,\"charging\":false}", LinkApi.telemetryPayload(0, false))
        assertEquals("{\"battery\":100,\"charging\":true}", LinkApi.telemetryPayload(100, true))
    }

    @Test
    fun clampsOutOfRange() {
        assertEquals("{\"battery\":0,\"charging\":false}", LinkApi.telemetryPayload(-5, false))
        assertEquals("{\"battery\":100,\"charging\":true}", LinkApi.telemetryPayload(150, true))
    }
}
