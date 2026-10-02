package dev.jarvis.link.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeConfigTest {
    @Test
    fun problemNoHost() {
        assertEquals(BridgeConfig.Problem.NO_HOST, BridgeConfig("", 4317, "tok").problem())
        assertEquals(BridgeConfig.Problem.NO_HOST, BridgeConfig("   ", 4317, "tok").problem())
    }

    @Test
    fun problemBadHost() {
        assertEquals(BridgeConfig.Problem.BAD_HOST, BridgeConfig("bad host!", 4317, "tok").problem())
        assertEquals(BridgeConfig.Problem.BAD_HOST, BridgeConfig("http://1.2.3.4", 4317, "tok").problem())
    }

    @Test
    fun problemBadPort() {
        assertEquals(BridgeConfig.Problem.BAD_PORT, BridgeConfig("1.2.3.4", 0, "tok").problem())
        assertEquals(BridgeConfig.Problem.BAD_PORT, BridgeConfig("1.2.3.4", 70000, "tok").problem())
    }

    @Test
    fun problemNoToken() {
        assertEquals(BridgeConfig.Problem.NO_TOKEN, BridgeConfig("1.2.3.4", 4317, "").problem())
        assertEquals(BridgeConfig.Problem.NO_TOKEN, BridgeConfig("1.2.3.4", 4317, "   ").problem())
    }

    @Test
    fun problemNullWhenComplete() {
        assertNull(BridgeConfig("100.77.6.93", 4317, "tok").problem())
        assertTrue(BridgeConfig("100.77.6.93", 4317, "tok").configured)
        assertFalse(BridgeConfig("", 4317, "tok").configured)
    }

    @Test
    fun endpointAndBaseUrl() {
        val v4 = BridgeConfig("100.77.6.93", 4317, "t")
        assertEquals("100.77.6.93:4317", v4.endpoint)
        assertEquals("http://100.77.6.93:4317", v4.baseUrl)
    }

    @Test
    fun endpointBracketsIpv6() {
        val v6 = BridgeConfig("::1", 4317, "t")
        assertEquals("[::1]:4317", v6.endpoint)
        assertEquals("http://[::1]:4317", v6.baseUrl)
        assertEquals("[fe80::1]", BridgeConfig.hostForUrl("fe80::1"))
        assertEquals("example.com", BridgeConfig.hostForUrl("example.com"))
    }

    @Test
    fun isValidHost() {
        assertTrue(BridgeConfig.isValidHost("100.77.6.93"))
        assertTrue(BridgeConfig.isValidHost("mypc"))
        assertTrue(BridgeConfig.isValidHost("my-pc.lan"))
        assertTrue(BridgeConfig.isValidHost("::1"))
        assertTrue(BridgeConfig.isValidHost("fe80::1"))
        assertFalse(BridgeConfig.isValidHost(""))
        assertFalse(BridgeConfig.isValidHost("   "))
        assertFalse(BridgeConfig.isValidHost("bad host"))
        assertFalse(BridgeConfig.isValidHost("host!"))
        assertFalse(BridgeConfig.isValidHost("-bad"))
        assertFalse(BridgeConfig.isValidHost("bad-"))
    }

    @Test
    fun normalizeHostStripsSchemePathAndPort() {
        assertEquals(BridgeConfig.HostInput("100.77.6.93", 4317), BridgeConfig.normalizeHost("http://100.77.6.93:4317/api"))
        assertEquals(BridgeConfig.HostInput("100.77.6.93", 8080), BridgeConfig.normalizeHost("100.77.6.93:8080"))
        assertEquals(BridgeConfig.HostInput("mypc", null), BridgeConfig.normalizeHost("  mypc  "))
        assertEquals(BridgeConfig.HostInput("myhost", null), BridgeConfig.normalizeHost("https://myhost/path?q=1"))
    }

    @Test
    fun normalizeHostBracketedIpv6() {
        assertEquals(BridgeConfig.HostInput("::1", 4317), BridgeConfig.normalizeHost("[::1]:4317"))
        assertEquals(BridgeConfig.HostInput("::1", null), BridgeConfig.normalizeHost("[::1]"))
    }

    @Test
    fun normalizeHostNonNumericPortKeepsWhole() {
        assertEquals(BridgeConfig.HostInput("host:abc", null), BridgeConfig.normalizeHost("host:abc"))
    }

    @Test
    fun normalizeToken() {
        assertEquals("abc123", BridgeConfig.normalizeToken("Bearer abc123"))
        assertEquals("abc123", BridgeConfig.normalizeToken("bearer abc123"))
        assertEquals("abc123", BridgeConfig.normalizeToken("Bearer   abc123  "))
        assertEquals("abc123", BridgeConfig.normalizeToken("\"abc123\""))
        assertEquals("abc123", BridgeConfig.normalizeToken("'abc123'"))
        assertEquals("abc123", BridgeConfig.normalizeToken("  abc123  "))
        assertEquals("abc123", BridgeConfig.normalizeToken("\"Bearer abc123\""))
    }

    @Test
    fun isValidToken() {
        assertTrue(BridgeConfig.isValidToken("abc123"))
        assertTrue(BridgeConfig.isValidToken("abc-123_XYZ.~"))
        assertFalse(BridgeConfig.isValidToken(""))
        assertFalse(BridgeConfig.isValidToken("has space"))
        assertFalse(BridgeConfig.isValidToken("tab\there"))
        assertFalse(BridgeConfig.isValidToken("tök"))
    }
}
