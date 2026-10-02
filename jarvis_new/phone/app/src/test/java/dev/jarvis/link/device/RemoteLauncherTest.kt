package dev.jarvis.link.device

import android.content.Context
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RemoteLauncherTest {
    @Test
    fun rdpUriShapes() {
        assertEquals(
            "rdp://full%20address=s:100.77.6.93:3389",
            RemoteLauncher.rdpUri("100.77.6.93", 3389),
        )
        assertEquals(
            "rdp://full%20address=s:10.0.0.5:3390",
            RemoteLauncher.rdpUri("  10.0.0.5  ", 3390),
        )
    }

    @Test
    fun rdpUriDefaults() {
        assertEquals("rdp://full%20address=s:h:3389", RemoteLauncher.rdpUri("h", null))
        assertEquals("rdp://full%20address=s::3389", RemoteLauncher.rdpUri(null, null))
        assertEquals("rdp://full%20address=s::3389", RemoteLauncher.rdpUri("   ", 3389))
    }

    @Test
    fun knownClientsOrder() {
        val pkgs = RemoteLauncher.KNOWN_CLIENTS.map { it.second }
        assertEquals(
            listOf(
                "com.carriez.flutter_hbb",
                "com.iiordanov.freeaRDP",
                "com.microsoft.rdc.androidx",
                "com.freerdp.afreerdp",
                "com.limelight",
            ),
            pkgs,
        )
    }

    @Test
    fun rustdeskUriUsesTailnetIp() {
        assertEquals("rustdesk://100.77.6.93", RemoteLauncher.rustdeskUri(" 100.77.6.93 "))
    }

    @Test
    fun labelForKnownAndUnknown() {
        assertEquals("Windows App", RemoteLauncher.labelFor("com.microsoft.rdc.androidx"))
        assertEquals("aFreeRDP", RemoteLauncher.labelFor("com.freerdp.afreerdp"))
        assertEquals("com.example.other", RemoteLauncher.labelFor("com.example.other"))
    }

    @Test
    fun isRemoteStartGate() {
        assertFalse(RemoteLauncher.isRemoteStart(null))
        assertFalse(RemoteLauncher.isRemoteStart(JSONObject()))
        assertFalse(
            RemoteLauncher.isRemoteStart(
                JSONObject().put("tool", "remote_start").put("ok", false)
            )
        )
        assertFalse(
            RemoteLauncher.isRemoteStart(
                JSONObject().put("tool", "lock").put("ok", true)
            )
        )
        assertTrue(
            RemoteLauncher.isRemoteStart(
                JSONObject().put("tool", "remote_start").put("ok", true)
            )
        )
    }

    @Test
    fun parseRemoteStartTopLevel() {
        val (host, port) = RemoteLauncher.parseRemoteStart(
            JSONObject()
                .put("tool", "remote_start")
                .put("ok", true)
                .put("host", "100.77.6.93")
                .put("port", 3389)
        ) ?: (null to null)
        assertEquals("100.77.6.93", host)
        assertEquals(3389, port)
    }

    @Test
    fun parseRemoteStartNestedArgsAndDefaultPort() {
        val (host, port) = RemoteLauncher.parseRemoteStart(
            JSONObject()
                .put("tool", "remote_start")
                .put("ok", true)
                .put("args", JSONObject().put("host", "10.0.0.5"))
        ) ?: (null to null)
        assertEquals("10.0.0.5", host)
        assertEquals(3389, port)
    }

    @Test
    fun parseRemoteStartRejectsNonActions() {
        assertNull(RemoteLauncher.parseRemoteStart(null))
        assertNull(RemoteLauncher.parseRemoteStart(JSONObject().put("tool", "lock")))
        assertNull(
            RemoteLauncher.parseRemoteStart(
                JSONObject().put("tool", "remote_start").put("ok", false)
            )
        )
    }

    @Test
    fun handleActionIgnoresNonActionsWithoutContext() {
        val noCtx: Context? = null
        assertFalse(RemoteLauncher.handleAction(noCtx, null))
        assertFalse(RemoteLauncher.handleAction(noCtx, JSONObject()))
        assertFalse(
            RemoteLauncher.handleAction(
                noCtx, JSONObject().put("tool", "lock").put("ok", true)
            )
        )
    }

    @Test
    fun handleActionConsumesRemoteStartWithoutContext() {
        // Null context: open() fails soft (false) but the action is consumed.
        val noCtx: Context? = null
        assertTrue(
            RemoteLauncher.handleAction(
                noCtx,
                JSONObject()
                    .put("tool", "remote_start")
                    .put("ok", true)
                    .put("host", "100.77.6.93")
                    .put("port", 3389),
            )
        )
        assertFalse(RemoteLauncher.open(noCtx, "100.77.6.93", 3389))
        assertNull(RemoteLauncher.pickClient(noCtx))
    }
}
