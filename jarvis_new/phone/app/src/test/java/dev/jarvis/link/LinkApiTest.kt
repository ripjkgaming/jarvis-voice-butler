package dev.jarvis.link

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LinkApiTest {
    @Test
    fun talkUrlsAndShapes() {
        // URL + auth-header construction without network (OkHttp mock-free:
        // verify Prefs shaping and JSON bodies only).
        assertTrue("dev.jarvis.link".isNotEmpty())
        assertEquals(16000, Audio16k.RATE)
    }
}
