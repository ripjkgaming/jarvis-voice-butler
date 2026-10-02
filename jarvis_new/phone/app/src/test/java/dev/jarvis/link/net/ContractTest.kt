package dev.jarvis.link.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Cross-checks the phone's constants against bridge.py itself so a renamed
 * tool or a changed guest list fails here instead of on a device.
 */
class ContractTest {
    private val bridge: String? by lazy {
        generateSequence(File("").absoluteFile) { it.parentFile }
            .map { File(it, "jarvis_new/src/bridge.py") }
            .firstOrNull { it.isFile }?.readText()
            ?: generateSequence(File("").absoluteFile) { it.parentFile }
                .map { File(it, "src/bridge.py") }.firstOrNull { it.isFile }?.readText()
    }

    private fun phoneToolBlock(src: String): String {
        val start = src.indexOf("def run_phone_tool")
        val end = src.indexOf("def _phone_cam_dir")
        return src.substring(start, end)
    }

    private val ourTools = listOf(
        Tool.VOLUME_GET, Tool.VOLUME_UP, Tool.VOLUME_DOWN, Tool.VOLUME_SET, Tool.VOLUME_MUTE, Tool.VOLUME_UNMUTE,
        Tool.MEDIA_PLAY_PAUSE, Tool.MEDIA_NEXT, Tool.MEDIA_PREV, Tool.PLAY_MEDIA, Tool.OPEN_APP, Tool.CLOSE_APP,
        Tool.SCREENSHOT, Tool.NOTIFY, Tool.LOCK, Tool.UNLOCK, Tool.SCREENS_STATE, Tool.SCREEN_OFF, Tool.SCREEN_ON,
        Tool.SCREENS_RESTORE, Tool.REMOTE_START, Tool.REMOTE_STOP, Tool.REMOTE_STATUS,
    )

    @Test
    fun everyToolNameExistsInRunPhoneTool() {
        val src = bridge
        assumeTrue("bridge.py not found from the test working directory", src != null)
        val block = phoneToolBlock(src!!)
        for (t in ourTools) {
            assertTrue("bridge.py has no tool '$t'", block.contains("\"$t\""))
        }
    }

    @Test
    fun theBridgeHasNoScreensOffTool() {
        val src = bridge
        assumeTrue(src != null)
        // The old app called this name and always failed: keep it from creeping back.
        assertTrue(!phoneToolBlock(src!!).contains("\"screens_off\""))
    }

    @Test
    fun guestAllowListMatchesTheBridge() {
        val src = bridge
        assumeTrue(src != null)
        val start = src!!.indexOf("_GUEST_VOICE_TOOLS = frozenset(")
        val block = src.substring(start, src.indexOf(")", start + 40))
        val names = Regex("\"([a-z_]+)\"").findAll(block).map { it.groupValues[1] }.toSet()
        assertEquals(names, GuestPolicy.ALLOWED_TOOLS)
    }

    @Test
    fun limitsMatchTheBridgeConstants() {
        val src = bridge
        assumeTrue(src != null)
        assertTrue(src!!.contains("_TYPE_MAX = ${Limits.TYPE_MAX}"))
        assertTrue(src.contains("_TALK_AUDIO_MAX = 2 * 1024 * 1024"))
        assertTrue(src.contains("len(text) > 2000"))
        assertTrue(src.contains("len(seed) > ${Limits.SEED_MAX}"))
        assertTrue(src.contains("len(text) > ${Limits.ROUTE_MAX}"))
        assertTrue(src.contains("len(key) > ${Limits.KEY_MAX}"))
        assertTrue(src.contains("len(pcm) < ${Limits.TALK_MIN_BYTES}"))
        assertTrue(src.contains("8 * 1024 * 1024"))
        assertTrue(src.contains("0 <= level <= ${Limits.VOLUME_MAX}"))
    }

    @Test
    fun keyNamesAreXkbNames() {
        assertEquals("Return", Keys.ENTER)
    }
}
