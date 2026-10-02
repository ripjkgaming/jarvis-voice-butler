package dev.jarvis.link.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PhoneLinksTest {
    @Test
    fun normalizeNumberStripsSeparators() {
        assertEquals("+15551234567", PhoneLinks.normalizeNumber("+1 (555) 123-4567"))
        assertEquals("5551234", PhoneLinks.normalizeNumber("555-1234"))
        assertEquals("5551234", PhoneLinks.normalizeNumber("  555 1234  "))
        assertEquals("5551234", PhoneLinks.normalizeNumber("555.1234"))
        assertEquals("*123#", PhoneLinks.normalizeNumber("*123#"))
    }

    @Test
    fun normalizeNumberRejects() {
        assertNull(PhoneLinks.normalizeNumber(""))
        assertNull(PhoneLinks.normalizeNumber("   "))
        assertNull(PhoneLinks.normalizeNumber("555-ABCD"))
        assertNull(PhoneLinks.normalizeNumber("call me"))
        assertNull(PhoneLinks.normalizeNumber("+"))
        assertNull(PhoneLinks.normalizeNumber("***"))
        assertNull(PhoneLinks.normalizeNumber("1+2"))
        assertNull(PhoneLinks.normalizeNumber("++1"))
        assertNull(PhoneLinks.normalizeNumber("555\t1234"))
    }

    @Test
    fun plusOnlyFirst() {
        assertEquals("+1555", PhoneLinks.normalizeNumber("+1555"))
        assertNull(PhoneLinks.normalizeNumber("1+555"))
        assertNull(PhoneLinks.normalizeNumber("+ 1+2"))
    }

    @Test
    fun dialUriEncodesHash() {
        assertEquals("tel:%23123", PhoneLinks.dialUri("#123"))
        assertEquals("tel:+15551234567", PhoneLinks.dialUri("+15551234567"))
    }

    @Test
    fun smsUri() {
        assertEquals("smsto:5551234", PhoneLinks.smsUri("5551234"))
    }

    @Test
    fun whatsappUriDigitsOnlyAndEncodedText() {
        assertEquals(
            "https://wa.me/15551234567?text=hello%20world",
            PhoneLinks.whatsappUri("+1 (555) 123-4567", "hello world"),
        )
        assertEquals("https://wa.me/15551234567", PhoneLinks.whatsappUri("15551234567", ""))
    }

    @Test
    fun whatsappUriTooShortReturnsNull() {
        assertNull(PhoneLinks.whatsappUri("123", "hi"))
        assertNull(PhoneLinks.whatsappUri("123456", "hi"))
        assertNull(PhoneLinks.whatsappUri("+1-23-456", "hi"))
    }
}
